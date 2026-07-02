"""Smoke test: render the whole dashboard as admin and fail on ANY exception.

Runs the page through Streamlit's AppTest against the local fake seam (fake ES,
fake vault, seeded local git) — no VPN, no Docker, no real services. This is the
regression net that would have caught crashes like the architecture-diff
``TypeError: '<' not supported between 'int' and 'NoneType'``.

It opens each lazy-loaded admin tab (by pre-setting the gate flags) so their
render paths execute too, not just the default Inventory tab.

Run:  pytest localdev/test_smoke.py -q       (after: python localdev/seed_git.py)
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("LOCALDEV_SECRETS", os.path.join(_HERE, "secrets.local.json"))
os.environ.setdefault("CICD_REPO_BASE", os.path.join(_HERE, "clones"))
os.environ.setdefault("DOCCHAT_OLLAMA_URL", "http://localhost:0")
os.environ.setdefault("LOCALDEV_ADO_FIXTURE",
                      os.path.join(_HERE, "fixtures", "ado_snapshot.json"))
_GITSRV = os.path.join(_HERE, "gitsrv").replace("\\", "/")
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = f"url.{_GITSRV}/.insteadof"
os.environ["GIT_CONFIG_VALUE_0"] = "http://LOCALDEVHOST/"

from streamlit.testing.v1 import AppTest  # noqa: E402

# Lazy-tab gate flags the dashboard checks before rendering each secondary tab.
_TAB_FLAGS = [
    "_tab_open_teams_v1", "_tab_open_eventlog_v1", "_tab_open_actions_v1",
    "_tab_open_sync_v1", "_tab_open_history_v1", "_tab_open_ado_v1",
    "_tab_open_arch_v1", "_tab_open_tp_v1",
]

_ADMIN_SESSION = {
    "user_roles": {"admin": True},
    "teams": ["DEVJAVA"],
    "company": "ACME",
    "username": "localdev",
    "email": "localdev@example.com",
    "admin_view_all": True,
}


def _new_app() -> AppTest:
    at = AppTest.from_file(os.path.join(_ROOT, "cicd_dashboard.py"), default_timeout=120)
    for k, v in _ADMIN_SESSION.items():
        at.session_state[k] = v
    return at


def _assert_clean(at: AppTest) -> None:
    if at.exception:
        msgs = "\n".join(f"{e.type}: {e.message}" for e in at.exception)
        pytest.fail(f"Dashboard raised during render:\n{msgs}")


def test_default_render():
    """The default (Inventory) view renders with no exception."""
    at = _new_app().run()
    _assert_clean(at)


def test_all_admin_tabs_render():
    """Every admin tab's body renders (gates pre-opened) with no exception."""
    at = _new_app()
    for f in _TAB_FLAGS:
        at.session_state[f] = True
    at.run()
    _assert_clean(at)
