"""Self-contained packaging, dependency, app-entrypoint, and Alembic checks.

This backend extract has no outer repository shim or CI workflow; checks here
validate files shipped in this backend and its repository-root Docker context.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path


def _find_inner_backend() -> Path:
    """Locate the ``orb_ai_backend`` package directory.

    Resolution order (most specific → least):
      1. ``ORB_INNER_BACKEND`` env override.
      2. Repo-relative walk from this test file — works from any
         extraction directory.
      3. Legacy hardcoded Emergent-preview paths (kept for backward
         compatibility with pre-1.1.0 checkouts).
    """
    override = os.environ.get("ORB_INNER_BACKEND")
    if override and Path(override).is_dir():
        return Path(override)

    here = Path(__file__).resolve()
    # This file lives at <inner_backend>/tests/test_shim_verification.py,
    # so the parent-of-parent is the inner backend directly.
    for parent in here.parents:
        if (parent / "app" / "main.py").is_file():
            return parent
        candidate = parent / "orb_ai_backend"
        if (candidate / "app" / "main.py").is_file():
            return candidate

    for legacy in (
        Path("/app/orb_audit/orb-Ai-v10-main-10/orb-ai/orb_ai_backend"),
        Path("/app/orb_ai_backend"),
    ):
        if (legacy / "app" / "main.py").is_file():
            return legacy
    # Fall back so the test surfaces the real error path.
    return Path(__file__).resolve().parent.parent


INNER_BACKEND = _find_inner_backend()
REQ_CORE = INNER_BACKEND / "requirements.txt"
REQ_AI = INNER_BACKEND / "requirements-ai.txt"
DOCKERFILE = INNER_BACKEND / "Dockerfile"


# --- C-1: outer shim identity, no self-routes, title, route count ----------

def _load_inner_app():
    # Ensure inner backend on path
    sys.path.insert(0, str(INNER_BACKEND))
    if "app.main" in sys.modules:
        del sys.modules["app.main"]
    return importlib.import_module("app.main").app


def _load_outer_shim_app():
    # Isolate shim import path
    sys.path.insert(0, str(OUTER_SHIM.parent))
    if "server" in sys.modules:
        del sys.modules["server"]
    spec = importlib.util.spec_from_file_location("orb_outer_shim", OUTER_SHIM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


def test_production_asgi_module_exports_stable_app():
    inner = _load_inner_app()
    imported = importlib.import_module("app.main").app
    assert imported is inner, "The production ASGI entrypoint must expose app.main:app"


def test_production_dockerfile_starts_the_application():
    """Validate the shipped container entrypoint; no outer shim is shipped."""
    text = DOCKERFILE.read_text()
    assert "app.main:app" in text
    assert "0.0.0.0" in text
    assert "$PORT" in text or "${PORT" in text


def test_c1_app_title_is_orb_ai():
    app = _load_inner_app()
    assert app.title == "ORB AI", f"Expected title 'ORB AI', got {app.title!r}"


def test_c1_app_exposes_at_least_128_routes():
    """v1.0.0 baseline was 128 routes; v1.1.0 adds more (permissions,
    trials, plans, strategy-catalog). Guardrail: routes must never drop
    below the v1.0.0 baseline."""
    app = _load_inner_app()
    n = len(app.routes)
    assert n >= 128, f"Expected >=128 routes (v1.0.0 baseline), got {n}"


# --- C-2: requirements split ------------------------------------------------

def test_c2_core_requirements_has_no_private_index():
    text = REQ_CORE.read_text()
    assert "--extra-index-url" not in text, "Core requirements.txt must not have --extra-index-url"
    assert "emergentintegrations" not in text.lower()
    assert "cloudfront" not in text.lower()


def test_c2_ai_requirements_exists_with_extra_index_and_pkg():
    assert REQ_AI.exists(), "requirements-ai.txt must exist"
    text = REQ_AI.read_text()
    assert "--extra-index-url" in text
    assert "emergentintegrations" in text.lower()


def test_c2_dockerfile_installs_both_with_fallback():
    text = DOCKERFILE.read_text()
    assert "pip install -r requirements.txt" in text
    assert "pip install -r requirements-ai.txt" in text
    # `|| echo` fallback on the AI line
    ai_block = re.search(
        r"pip install -r requirements-ai\.txt[^\n]*\n?\s*\|\|\s*echo",
        text,
    )
    assert ai_block is not None, "AI extras install must have `|| echo` fallback"


def test_c2_pytest_configuration_is_shipped_with_backend():
    """Test configuration is local to this extract; CI lives outside it."""
    assert (INNER_BACKEND / "pytest.ini").is_file()
    assert (INNER_BACKEND / "tests" / "conftest.py").is_file()


def test_c2_import_app_main_succeeds_without_ai_extras():
    """Core-only install must be able to import app.main (rule-based fallback)."""
    app = _load_inner_app()
    assert app is not None
    assert app.title == "ORB AI"


# --- Alembic chain integrity -----------------------------------------------

def test_alembic_chain_is_sequential_and_linear():
    """v1.0.0 shipped with 9 migrations; v1.1.0 adds 0010_v110_foundation.
    Guardrail: chain must remain linear (1 tail, 1 head, no forks) and
    have at least the v1.0.0 baseline count of 9.
    """
    versions_dir = INNER_BACKEND / "alembic" / "versions"
    files = sorted(p.name for p in versions_dir.glob("*.py"))
    assert len(files) >= 9, f"Expected >=9 migrations (v1.0.0 baseline), got {len(files)}: {files}"

    # Parse revision / down_revision from each file to walk the chain
    revs = {}
    for p in versions_dir.glob("*.py"):
        text = p.read_text()
        m_rev = re.search(r"^revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
        m_down = re.search(r"^down_revision(?::\s*[^=]+)?\s*=\s*(None|['\"]([^'\"]+)['\"])", text, re.MULTILINE)
        assert m_rev, f"No revision in {p.name}"
        rev = m_rev.group(1)
        down = None if (m_down and m_down.group(1) == "None") else (m_down.group(2) if m_down else None)
        revs[rev] = down

    # Exactly one tail (down_revision == None) and one head
    tails = [r for r, d in revs.items() if d is None]
    assert len(tails) == 1, f"Expected exactly 1 tail, got {tails}"

    down_targets = {d for d in revs.values() if d}
    heads = [r for r in revs if r not in down_targets]
    assert len(heads) == 1, f"Expected exactly 1 head, got {heads}"

    # Walk chain from tail
    seen = 0
    cur = tails[0]
    while cur is not None:
        seen += 1
        children = [r for r, d in revs.items() if d == cur]
        assert len(children) <= 1, f"Fork at {cur}: {children}"
        cur = children[0] if children else None
    assert seen == len(revs), f"Chain walk covered {seen}/{len(revs)} nodes — orphan detected"
