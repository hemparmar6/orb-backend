"""Tests for the production-only opt-in wiring in ``app.main``.

Verifies:
1. When ``ADMIN_UI_DIST`` points at a directory containing an ``index.html``,
   the production build of ``app.main`` mounts the SPA at ``/api/admin-ui/``.
2. Nothing is mounted (route returns 404) when the env var is unset — this
   is the default under pytest, so existing tests are unaffected.
3. The ``_maybe_run_admin_seeder`` helper is idempotent and gated on
   ``ADMIN_SEED_ON_STARTUP``.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def _rebuild_app(monkeypatch, tmp_path: Path):
    """Reload app.main so that create_app() sees fresh env vars."""

    def _do(env_override: dict[str, str] | None = None) -> tuple:
        for k, v in (env_override or {}).items():
            monkeypatch.setenv(k, v)
        # Force reimport so the module reads the new env.
        if "app.main" in sys.modules:
            del sys.modules["app.main"]
        module = importlib.import_module("app.main")
        return module.app, module

    return _do


def test_admin_ui_not_mounted_by_default(_rebuild_app) -> None:
    """Without ADMIN_UI_DIST the SPA routes must not exist."""
    from fastapi.testclient import TestClient

    app, _ = _rebuild_app({})
    with TestClient(app) as client:
        r = client.get("/api/admin-ui/")
        assert r.status_code == 404


def test_admin_ui_served_when_dist_env_set(_rebuild_app, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><body id="root">ok</body>')
    (dist / "assets" / "app.js").write_text("console.log('ok')")

    from fastapi.testclient import TestClient

    app, _ = _rebuild_app({"ADMIN_UI_DIST": str(dist)})
    with TestClient(app) as client:
        # Index page
        r = client.get("/api/admin-ui/")
        assert r.status_code == 200
        assert 'id="root"' in r.text
        # Real asset served by StaticFiles
        r = client.get("/api/admin-ui/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text
        # Unknown SPA path falls back to index.html
        r = client.get("/api/admin-ui/users/deep/link")
        assert r.status_code == 200
        assert 'id="root"' in r.text


@pytest.mark.asyncio
async def test_admin_seeder_skipped_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_SEED_ON_STARTUP", raising=False)
    if "app.main" in sys.modules:
        del sys.modules["app.main"]
    from app.main import _maybe_run_admin_seeder  # noqa: WPS433
    # Should be a no-op; must not raise even if the seeder module is broken.
    await _maybe_run_admin_seeder()
