"""Measure dashboard render performance and write a report for CI.

Runs the page through AppTest against the local fake seam and times:
  - cold render   (first run; imports + all fetches)
  - warm rerun    (second run; caches warm — this is what a filter interaction
                   costs, and where the fragment/lazy-tab optimisations pay off)
Also captures the per-phase timeline the page dumps via LOCALDEV_PERF_DUMP, so
the report can name the slowest phases.

Writes localdev/ci_report/perf.json. Best-effort: never fails the build.

    python localdev/perf.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_OUT = os.path.join(_HERE, "ci_report")
_DUMP = os.path.join(_OUT, "_perf_phases.json")

for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("LOCALDEV_SECRETS", os.path.join(_HERE, "secrets.local.json"))
os.environ.setdefault("CICD_REPO_BASE", os.path.join(_HERE, "clones"))
os.environ.setdefault("DOCCHAT_OLLAMA_URL", "http://localhost:0")
os.environ["LOCALDEV_PERF_DUMP"] = _DUMP
_GITSRV = os.path.join(_HERE, "gitsrv").replace("\\", "/")
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = f"url.{_GITSRV}/.insteadof"
os.environ["GIT_CONFIG_VALUE_0"] = "http://LOCALDEVHOST/"

_ADMIN = {"user_roles": {"admin": True}, "teams": ["DEVJAVA"], "company": "ACME",
          "username": "localdev", "email": "localdev@example.com",
          "admin_view_all": True}


def _measure() -> dict:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(_ROOT, "cicd_dashboard.py"),
                           default_timeout=120)
    for k, v in _ADMIN.items():
        at.session_state[k] = v

    t0 = time.perf_counter()
    at.run()
    cold_ms = (time.perf_counter() - t0) * 1000.0
    cold_phases = _read_dump()

    # Warm rerun — caches now hot; this is the steady-state interaction cost.
    t1 = time.perf_counter()
    at.run()
    warm_ms = (time.perf_counter() - t1) * 1000.0

    n_el = 0
    for attr in ("markdown", "button", "selectbox", "dataframe", "metric"):
        try:
            n_el += len(getattr(at, attr))
        except Exception:
            pass

    top = sorted(cold_phases, key=lambda p: -p["ms"])[:6]
    return {
        "cold_render_ms": round(cold_ms, 1),
        "warm_rerun_ms": round(warm_ms, 1),
        "phase_total_ms": round(sum(p["ms"] for p in cold_phases), 1),
        "elements": n_el,
        "top_phases": top,
        "ok": True,
    }


def _read_dump() -> list:
    try:
        with open(_DUMP, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("phases", [])
    except Exception:
        return []


def main() -> int:
    os.makedirs(_OUT, exist_ok=True)
    try:
        rep = _measure()
    except Exception as exc:
        rep = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    with open(os.path.join(_OUT, "perf.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    if rep.get("ok"):
        print(f"[perf] cold {rep['cold_render_ms']:.0f}ms · "
              f"warm rerun {rep['warm_rerun_ms']:.0f}ms · "
              f"{rep['elements']} elements")
        for p in rep["top_phases"]:
            print(f"       {p['ms']:7.1f}ms  {p['label']}")
    else:
        print(f"[perf] skipped: {rep.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
