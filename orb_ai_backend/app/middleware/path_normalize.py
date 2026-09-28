"""Path-normalization middleware.

FastAPI's default ``redirect_slashes=True`` emits a **307 Temporary Redirect**
when a request path's trailing slash convention does not match a registered
route (e.g. ``GET /api/v1/notifications/`` → 307 → ``GET /api/v1/notifications``).

Most HTTP clients drop the ``Authorization`` header on cross-origin redirects
(browsers, ``httpx`` with ``follow_redirects=False``, React Native's ``fetch``),
which manifests as a silent 401 for authenticated endpoints — reported by
the ORB AI mobile client on ``/api/v1/notifications/``.

This middleware fixes the class of bug at the framework level without
changing any route contract:

* On every incoming HTTP request, if the requested ``path`` does not match
  any registered route BUT its slash-flipped alternative does, we rewrite
  the ASGI scope so the request routes to the correct handler in-process.
* No HTTP redirect is ever emitted, so no header is dropped.
* If both variants are 404 (path truly doesn't exist), the request is left
  alone and FastAPI's normal 404 handling kicks in.
* If the requested path already matches a route, we do nothing.

Pair this middleware with ``app.router.redirect_slashes = False`` (done in
``app/main.py``) so the built-in 307 path is fully disabled.

Backward compatibility: every existing (path, method) tuple continues to
resolve to the same handler that FastAPI's 307 redirect used to reach.
Only the wire-level 307 hop is removed.
"""
from __future__ import annotations

from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send


class PathNormalizationMiddleware:
    """Rewrite trailing-slash mismatches instead of emitting a 307."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not path or path == "/":
            await self.app(scope, receive, send)
            return

        # Fast path: the request already matches a registered route.
        if self._matches(scope, path):
            await self.app(scope, receive, send)
            return

        # Try the slash-flipped alternative.
        alt = path[:-1] if path.endswith("/") else path + "/"
        if alt and alt != "/" and self._matches(scope, alt):
            new_scope = dict(scope)
            new_scope["path"] = alt
            # Keep raw_path in sync so downstream middleware / logs see the
            # normalized path, not the original.
            new_scope["raw_path"] = alt.encode("utf-8")
            scope = new_scope

        await self.app(scope, receive, send)

    def _matches(self, scope: Scope, path: str) -> bool:
        """Return True iff at least one registered route fully matches ``path``.

        We build a shallow, mutation-free scope with the candidate path and
        ask each Starlette ``Route`` whether it matches. This handles path
        parameters (``/bots/{bot_id}``) correctly — a plain set-membership
        check would not.
        """
        router = getattr(self.app, "router", None) or getattr(self.app, "app", None)
        if router is None:
            # If the ASGI app doesn't expose a router (unusual), bail out.
            return False
        routes = getattr(router, "routes", None)
        if not routes:
            # Try one level deeper (FastAPI wraps its router in an app).
            inner = getattr(router, "router", None)
            routes = getattr(inner, "routes", None) if inner is not None else None
        if not routes:
            return False

        probe = {**scope, "path": path}
        # Starlette Route.matches expects raw_path too for some routing.
        probe["raw_path"] = path.encode("utf-8")
        for route in routes:
            try:
                match, _ = route.matches(probe)
            except Exception:  # pragma: no cover - defensive
                continue
            if match == Match.FULL:
                return True
        return False
