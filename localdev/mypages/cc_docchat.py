# Expose the repo-root cc_docchat.py as `mypages.cc_docchat` (the import the
# dashboard uses). We exec the root file into THIS module's namespace so
# `from mypages.cc_docchat import render_docchat_panel` resolves to it, with no
# symlinks (Windows-friendly) and no copy step (edits reflected immediately).
import os as _os

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_SRC = _os.path.join(_ROOT, "cc_docchat.py")
if _os.path.isfile(_SRC):
    with open(_SRC, "r", encoding="utf-8") as _fh:
        exec(compile(_fh.read(), _SRC, "exec"))
else:  # pragma: no cover - docchat optional
    def render_docchat_panel(*args, **kwargs):
        import streamlit as st
        st.info("cc_docchat.py not found at repo root — docchat disabled in localdev.")
