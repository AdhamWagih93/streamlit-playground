"""Local launcher for the CI/CD dashboard — run with:

    streamlit run localdev/front_local.py

It wires the local fake seam (utils shim + fake vault/ES), redirects git clones
to the seeded local repos, injects an admin session so every feature is
visible, then runs the REAL repo-root cicd_dashboard.py verbatim. No VPN, no
Docker, no real services.
"""

import os
import sys
import runpy

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# localdev/ first on the path so `import utils` / `import mypages` resolve to the
# local shims; repo root second so the dashboard's own siblings still import.
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Fake-vault secrets + an isolated clone base so we never touch a real /tmp repo.
os.environ.setdefault("LOCALDEV_SECRETS", os.path.join(_HERE, "secrets.local.json"))
os.environ.setdefault("CICD_REPO_BASE", os.path.join(_HERE, "clones"))
# Keep Ollama/docchat pointed at nothing reachable by default (graceful).
os.environ.setdefault("DOCCHAT_OLLAMA_URL", "http://localhost:0")

# Redirect every `http://LOCALDEVHOST/...` git clone to the seeded local repos
# under localdev/gitsrv/ — scoped to THIS process via GIT_CONFIG_* env (git
# 2.31+), so no global git config is touched. Cross-platform (forward slashes
# work on Windows too).
_GITSRV = os.path.join(_HERE, "gitsrv").replace("\\", "/")
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = f"url.{_GITSRV}/.insteadof"
os.environ["GIT_CONFIG_VALUE_0"] = "http://LOCALDEVHOST/"

import streamlit as st  # noqa: E402

# The real front.py login normally sets these. Inject an admin session so all
# tabs/features render. Idempotent across Streamlit reruns (setdefault).
st.session_state.setdefault("user_roles", {"admin": True})
st.session_state.setdefault("teams", ["DEVJAVA"])
st.session_state.setdefault("company", "ACME")
st.session_state.setdefault("username", "localdev")
st.session_state.setdefault("email", "localdev@example.com")
st.session_state.setdefault("admin_view_all", True)

# Run the real dashboard (repo-root file) as the script. It calls
# st.set_page_config first, which is allowed because we've only touched
# session_state so far (not an st render command).
runpy.run_path(os.path.join(_ROOT, "cicd_dashboard.py"), run_name="__main__")
