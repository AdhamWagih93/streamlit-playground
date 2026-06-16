"""cc_docchat.py — isolated document-chat assistant panel for the CI/CD dashboard.

A self-contained, always-visible floating chat panel that the dashboard mounts
via a SINGLE call (`render_docchat_panel()`) inside an `@st.fragment`, so every
chat interaction reruns ONLY this panel — never the ~28k-line dashboard. Nothing
here executes on import (no page config, no module-level render), so importing it
is free.

It mirrors `doc_chat.py`'s backend exactly:
  • LLM   — local Ollama (`/api/chat`, streaming) via `requests`.
  • Logs  — Postgres `public.chatbot_history`, identical schema + insert.

Dashboard-specific: instead of file uploads, context comes from the cloned
**DocMDs** repo (`<CICD_REPO_BASE>/DocMDs/<repository_name>/*.md`). Any in-scope
application whose name/repository matches a DocMDs folder can be selected; its
markdown files are listed and folded into the LLM context.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

import streamlit as st

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

# Postgres — psycopg v3 preferred, v2 fallback (matches the dashboard).
try:
    import psycopg as _pg  # type: ignore
    _PG_VARIANT = "v3"
except Exception:  # pragma: no cover
    try:
        import psycopg2 as _pg  # type: ignore
        import psycopg2.extras  # noqa: F401
        _PG_VARIANT = "v2"
    except Exception:
        _pg = None  # type: ignore
        _PG_VARIANT = ""

try:
    from utils.vault import VaultClient as _VaultClient  # type: ignore
except Exception:  # pragma: no cover
    _VaultClient = None  # type: ignore


# ── Config (env-overridable; defaults match doc_chat.py) ──────────────────────
OLLAMA_URL = os.environ.get("DOCCHAT_OLLAMA_URL", "http://ef-nexus-03:8081").rstrip("/")
MODEL = os.environ.get("DOCCHAT_MODEL", "qwen3.5:9b")
HISTORY_SCHEMA = os.environ.get("DOCCHAT_HISTORY_SCHEMA", "public")
HISTORY_TABLE = os.environ.get("DOCCHAT_HISTORY_TABLE", "chatbot_history")
POSTGRES_VAULT_PATH = os.environ.get("POSTGRES_VAULT_PATH", "postgres").strip()
_REPO_BASE = os.environ.get("CICD_REPO_BASE", "/tmp/cicd-dashboard").rstrip("/")
DOCMDS_DIR = os.path.join(_REPO_BASE, "DocMDs")
MAX_CTX_CHARS = 80_000          # per-document truncation, like doc_chat
MAX_TOTAL_CTX_CHARS = 200_000   # safety cap across all selected docs
_OLLAMA_TIMEOUT = (10, 300)


# =============================================================================
# DB — identical schema + insert to doc_chat.py (public.chatbot_history)
# =============================================================================
_db_config_cache: dict | None = None


def _get_db_config() -> dict | None:
    global _db_config_cache
    if _db_config_cache is not None:
        return _db_config_cache
    if _VaultClient is None:
        return None
    try:
        vc = _VaultClient()
        _db_config_cache = vc.read_all_nested_secrets(POSTGRES_VAULT_PATH)
        return _db_config_cache
    except Exception:
        return None


def _get_conn():
    if _pg is None:
        return None
    cfg = _get_db_config()
    if not cfg or not cfg.get("host"):
        return None
    try:
        conn = _pg.connect(
            host=cfg["host"], port=int(cfg.get("port") or 5432),
            dbname=cfg["database"], user=cfg["username"],
            password=cfg["password"], connect_timeout=5,
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


# Non-key columns of chatbot_history, with their definitions. Used both to
# create the table fresh and to additively migrate an older/partial table.
# Order matters only for a fresh CREATE; ADD COLUMN IF NOT EXISTS is order-free.
_HISTORY_COLUMNS: list[tuple[str, str]] = [
    ("session_id",    "TEXT NOT NULL DEFAULT ''"),
    ("username",      "TEXT"),
    ("role",          "TEXT NOT NULL DEFAULT 'user'"),
    ("content",       "TEXT NOT NULL DEFAULT ''"),
    ("timestamp_utc", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ("duration_s",    "NUMERIC"),
    ("tokens_est",    "INTEGER"),
    ("model",         "TEXT"),
    ("documents",     "TEXT[]"),
    ("chat_mode",     "TEXT DEFAULT 'normal'"),
    ("has_images",    "BOOLEAN DEFAULT FALSE"),
    ("has_error",     "BOOLEAN DEFAULT FALSE"),
    ("intent_score",  "INTEGER"),
]


def db_ensure_table() -> None:
    """Ensure chatbot_history exists AND has every expected column.

    Strictly additive and non-destructive:
      • ``CREATE TABLE IF NOT EXISTS`` never touches an existing table.
      • Each missing column is added with ``ALTER TABLE … ADD COLUMN IF NOT
        EXISTS`` — this only appends columns, never drops, renames, retypes, or
        rewrites existing rows. Columns carry the same defaults the schema would
        have used, so pre-existing rows are backfilled in place (metadata-only
        on PG 11+). No historic data is read, moved, or lost.

    Runs at most once per session (gated by ``_dc_table_ready``).
    """
    if st.session_state.get("_dc_table_ready"):
        return
    conn = _get_conn()
    if conn is None:
        return
    _fqtn = f"{HISTORY_SCHEMA}.{HISTORY_TABLE}"
    try:
        with conn.cursor() as cur:
            # 1) Create the table if it's entirely absent (existing tables are
            #    left exactly as-is by IF NOT EXISTS).
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_fqtn} (
                    id BIGSERIAL PRIMARY KEY
                )
                """
            )
            # 2) Additively reconcile columns — adds only what's missing.
            for _col, _ddl in _HISTORY_COLUMNS:
                try:
                    cur.execute(
                        f"ALTER TABLE {_fqtn} "
                        f"ADD COLUMN IF NOT EXISTS {_col} {_ddl}"
                    )
                except Exception:
                    # One column failing (e.g. a pre-existing column with a
                    # different but compatible definition) must not abort the
                    # rest. autocommit means each statement stands alone.
                    pass
        st.session_state["_dc_table_ready"] = True
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_save_message(msg: dict, session_id: str, username: str,
                    documents: list[str], chat_mode: str = "dashboard",
                    has_images: bool = False, has_error: bool = False,
                    intent_score: int | None = None) -> None:
    """Persist a single message — same columns/shape as doc_chat.py."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {HISTORY_SCHEMA}.{HISTORY_TABLE}
                    (session_id, username, role, content, timestamp_utc,
                     duration_s, tokens_est, model, documents, chat_mode,
                     has_images, has_error, intent_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id, username or "", msg["role"], msg["content"],
                    datetime.now(timezone.utc), msg.get("duration"),
                    msg.get("tokens"), MODEL, documents or [], chat_mode,
                    has_images, has_error, intent_score,
                ),
            )
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# LLM — Ollama streaming (verbatim behaviour from doc_chat.py)
# =============================================================================
def chat_stream(messages: list[dict], model: str | None = None):
    if requests is None:
        raise RuntimeError("requests not installed")
    payload = {"model": model or MODEL, "messages": messages, "stream": True}
    with requests.post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True,
                       timeout=_OLLAMA_TIMEOUT) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token:
                yield token
            if chunk.get("done"):
                break


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


# =============================================================================
# DocMDs — discover folders, match to in-scope apps, read markdown
# =============================================================================
@st.cache_data(ttl=120, show_spinner=False)
def _docmds_folders() -> dict[str, list[str]]:
    """{folder_name: [relative .md paths]} for every folder in the DocMDs
    clone. Cached briefly so the panel never re-walks the tree per keystroke."""
    out: dict[str, list[str]] = {}
    if not os.path.isdir(DOCMDS_DIR):
        return out
    try:
        for _entry in sorted(os.listdir(DOCMDS_DIR)):
            if _entry.startswith(".") or _entry == ".git":
                continue
            _fdir = os.path.join(DOCMDS_DIR, _entry)
            if not os.path.isdir(_fdir):
                continue
            _mds: list[str] = []
            for _root, _dirs, _files in os.walk(_fdir):
                if ".git" in _dirs:
                    _dirs.remove(".git")
                for _fn in _files:
                    if _fn.lower().endswith((".md", ".markdown")):
                        _rel = os.path.relpath(os.path.join(_root, _fn), _fdir)
                        _mds.append(_rel.replace(os.sep, "/"))
            out[_entry] = sorted(_mds, key=str.lower)
    except OSError:
        pass
    return out


@st.cache_data(ttl=120, show_spinner=False)
def _read_md(folder: str, rel: str) -> str:
    """Read one markdown file from the DocMDs clone (path-traversal guarded)."""
    base = os.path.realpath(os.path.join(DOCMDS_DIR, folder))
    target = os.path.realpath(os.path.join(base, rel))
    if target != base and not target.startswith(base + os.sep):
        return ""
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


# Names drift between the inventory (application / repository_name) and the
# DocMDs folder names in separator style as well as case ("My App" vs "my_app"
# vs "My-App"). Normalise both sides so a real match is never missed — the same
# spirit as the dashboard's team-name matching.
_NAME_SEP_RE = re.compile(r"[\s_\-]+")


def _norm_name(s) -> str:
    """Separator- and case-insensitive match key for an app/repo/folder name."""
    k = (str(s) if s is not None else "").strip().lower()
    return _NAME_SEP_RE.sub("_", k).strip("_") if k else ""


def _scope_rows() -> list[dict]:
    """Inventory rows in the current scope (published by the dashboard)."""
    return (st.session_state.get("_inv_rows_filtered_v1")
            or st.session_state.get("_inv_rows_all_v1") or [])


def _scope_projects() -> dict[str, list[dict]]:
    """``{project: [{"application", "repository_name"}, …]}`` for every project
    in the current inventory scope. The picker is project-based: the user picks
    a project and we auto-detect the DocMDs folders for its apps."""
    _out: dict[str, list[dict]] = {}
    for _r in _scope_rows():
        _proj = (_r.get("project") or "").strip()
        if not _proj:
            continue
        _out.setdefault(_proj, []).append({
            "application": (_r.get("application") or "").strip(),
            "repository_name": (_r.get("repository_name") or "").strip(),
        })
    return _out


def _folder_index() -> dict[str, str]:
    """``{normalised_folder_name: actual_folder_name}`` for the DocMDs clone."""
    return {_norm_name(_f): _f for _f in _docmds_folders()}


def _resolve_project_folders(projects: list[str]) -> tuple[list[str], list[dict]]:
    """For the selected *projects*, detect the DocMDs folder matching each
    application's ``repository_name`` (falling back to the application name).

    Returns ``(matched_folders_sorted, detail)`` where *detail* is one entry per
    app — ``{"project","application","repository_name","folder"}`` with
    ``folder`` = the matched DocMDs folder or ``""`` — so the UI can show both
    what was attached and which apps had no docs. Matching is case- and
    separator-tolerant (``repository_name`` → folder name)."""
    _fidx = _folder_index()
    _scope = _scope_projects()
    _matched: set[str] = set()
    _detail: list[dict] = []
    for _proj in projects:
        for _app in _scope.get(_proj, []):
            _repo = _app["repository_name"]
            _key = _norm_name(_repo) or _norm_name(_app["application"])
            _folder = _fidx.get(_key, "")
            _detail.append({
                "project": _proj,
                "application": _app["application"],
                "repository_name": _repo,
                "folder": _folder,
            })
            if _folder:
                _matched.add(_folder)
    return sorted(_matched, key=str.lower), _detail


# =============================================================================
# Prompt assembly
# =============================================================================
def _build_context(selected: list[str]) -> tuple[str, list[str]]:
    """Concatenate the markdown of every selected DocMDs folder into LLM
    context. Returns (context_text, doc_ids) where doc_ids feed the DB log."""
    _folders = _docmds_folders()
    _parts: list[str] = []
    _doc_ids: list[str] = []
    _budget = MAX_TOTAL_CTX_CHARS
    for _folder in selected:
        for _rel in _folders.get(_folder, []):
            if _budget <= 0:
                break
            _txt = _read_md(_folder, _rel)
            if not _txt:
                continue
            _txt = _txt[:MAX_CTX_CHARS]
            _txt = _txt[:_budget]
            _budget -= len(_txt)
            _doc_ids.append(f"{_folder}/{_rel}")
            _parts.append(f"### {_folder} / {_rel}\n```markdown\n{_txt}\n```\n")
    return "\n".join(_parts), _doc_ids


def build_system_prompt(context_text: str) -> str:
    _user = st.session_state.get("username", "")
    _title = st.session_state.get("title", "")
    _teams = st.session_state.get("teams", []) or []
    _parts = [
        "You are a professional documentation assistant embedded in a CI/CD "
        "platform dashboard. Answer the user's questions clearly and "
        "accurately, grounded in the provided documentation when relevant. "
        "If the answer isn't in the documents, say so rather than inventing.",
    ]
    _who = []
    if _user:
        _who.append(f"name: {_user}")
    if _title:
        _who.append(f"title: {_title}")
    if _teams:
        _who.append(f"teams: {', '.join(str(t) for t in _teams)}")
    if _who:
        _parts.append("The user you're assisting — " + "; ".join(_who) + ".")
    if context_text.strip():
        _parts.append(
            "\nThe user attached the following platform documentation "
            "(from the DocMDs repository). Use it to answer when relevant:\n"
        )
        _parts.append(context_text)
    else:
        _parts.append(
            "\nNo documentation is attached. Answer from general knowledge and "
            "invite the user to attach an application's docs for grounded answers."
        )
    return "\n".join(_parts)


# =============================================================================
# Render — a single @st.fragment the dashboard mounts once
# =============================================================================
def _init_state() -> None:
    st.session_state.setdefault("_dc_open", False)
    st.session_state.setdefault("_dc_messages", [])
    st.session_state.setdefault("_dc_selected_projects", [])
    st.session_state.setdefault("_dc_selected_apps", [])  # resolved folders
    if not st.session_state.get("_dc_session_id"):
        st.session_state["_dc_session_id"] = uuid.uuid4().hex


def _render_messages() -> None:
    _msgs = st.session_state["_dc_messages"]
    if not _msgs:
        st.markdown(
            '<div class="dc-empty">👋 Ask anything about your platform. '
            'Attach an application below to ground answers in its DocMDs.</div>',
            unsafe_allow_html=True,
        )
        return
    for _m in _msgs:
        with st.chat_message(_m["role"]):
            st.markdown(_m["content"])
            _meta = []
            if _m.get("timestamp"):
                _meta.append(_m["timestamp"])
            if _m.get("duration") is not None:
                _meta.append(f"{_m['duration']:.1f}s")
            if _m.get("tokens"):
                _meta.append(f"{_m['tokens']} tok")
            if _meta:
                st.markdown(
                    f'<div class="dc-meta">{html.escape(" · ".join(_meta))}</div>',
                    unsafe_allow_html=True,
                )


@st.fragment
def render_docchat_panel() -> None:
    """Mount the always-visible, fully-isolated doc-chat assistant.

    Decorated `@st.fragment`: typing, sending, selecting docs and toggling the
    panel rerun ONLY this fragment, so the surrounding dashboard is never
    re-executed — the integration is performance-invisible to the rest of the
    page."""
    _init_state()

    # Collapsed → just the floating launcher bubble.
    if not st.session_state["_dc_open"]:
        with st.container(key="cc_docchat_launcher"):
            if st.button("💬", key="_dc_open_btn",
                         help="Open the documentation assistant"):
                st.session_state["_dc_open"] = True
                st.rerun(scope="fragment")
        return

    db_ensure_table()

    with st.container(key="cc_docchat_panel"):
        # Header
        _h1, _h2 = st.columns([5, 1])
        with _h1:
            st.markdown(
                '<div class="dc-title">📚 Docs Assistant'
                f'<span class="dc-model">{html.escape(MODEL)}</span></div>',
                unsafe_allow_html=True,
            )
        with _h2:
            if st.button("✕", key="_dc_close_btn", help="Minimise"):
                st.session_state["_dc_open"] = False
                st.rerun(scope="fragment")

        # ── Context picker (PROJECT-based) ──────────────────────────────────
        # The user picks one or more projects from the current inventory scope;
        # for every application in those projects we auto-detect the DocMDs
        # folder matching its repository_name (case-/separator-tolerant) and
        # fold its markdown into the LLM context. The dropdown's option list is
        # forced above the panel's z-index (CSS) so the choices + app counts are
        # actually visible while picking — no typing from memory.
        _folders = _docmds_folders()
        _scope_proj = _scope_projects()
        _n_sel = len(st.session_state["_dc_selected_apps"])
        st.markdown(
            '<div class="dc-ctx-label">📎 Context'
            + (f'<span class="dc-ctx-n">{_n_sel} doc set'
               f'{"s" if _n_sel != 1 else ""}</span>' if _n_sel else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if not _folders:
            st.markdown(
                '<div class="dc-ctx-hint">DocMDs repository not cloned yet — it '
                "syncs with the other platform repos (Sync Check tab).</div>",
                unsafe_allow_html=True,
            )
        elif not _scope_proj:
            st.markdown(
                '<div class="dc-ctx-hint">No projects in scope yet — open the '
                "Pipelines Inventory so your projects load, then pick one here "
                "to attach its applications' docs.</div>",
                unsafe_allow_html=True,
            )
        else:
            _proj_opts = sorted(_scope_proj.keys(), key=str.lower)

            def _fmt_project(_p: str) -> str:
                _n = len(_scope_proj.get(_p, []))
                return f"{_p}  ·  {_n} app{'s' if _n != 1 else ''}"

            _sel_proj = st.multiselect(
                "Select project(s)",
                options=_proj_opts,
                default=[p for p in st.session_state["_dc_selected_projects"]
                         if p in _proj_opts],
                key="_dc_proj_ms",
                label_visibility="collapsed",
                placeholder="Select project(s) to attach their docs…",
                format_func=_fmt_project,
            )
            st.session_state["_dc_selected_projects"] = _sel_proj

            # Resolve DocMDs folders for the chosen projects' apps.
            _matched_folders, _detail = _resolve_project_folders(_sel_proj)
            st.session_state["_dc_selected_apps"] = _matched_folders  # → context

            _n_apps = len(_detail)
            _n_hit = sum(1 for _d in _detail if _d["folder"])
            if _sel_proj:
                _cap = (f"{_n_hit}/{_n_apps} application"
                        f"{'s' if _n_apps != 1 else ''} matched a DocMDs folder")
                st.markdown(f'<div class="dc-ctx-cap">{html.escape(_cap)}</div>',
                            unsafe_allow_html=True)

            # Attached docs — one block per matched folder with its files.
            for _folder in _matched_folders:
                _mds = _folders.get(_folder, [])
                _apps_here = sorted({_d["application"] for _d in _detail
                                     if _d["folder"] == _folder and _d["application"]},
                                    key=str.lower)
                _sub = (" · ".join(html.escape(_a) for _a in _apps_here)
                        if _apps_here else "")
                st.markdown(
                    f'<div class="dc-doc-app">📁 {html.escape(_folder)} '
                    f'<span class="dc-doc-n">{len(_mds)} file'
                    f'{"s" if len(_mds) != 1 else ""}</span></div>'
                    + (f'<div class="dc-doc-apps">{_sub}</div>' if _sub else "")
                    + '<div class="dc-doc-files">' + "".join(
                        f'<span class="dc-doc-file">⬡ {html.escape(_f)}</span>'
                        for _f in _mds
                    ) + ('<span class="dc-doc-file is-none">no .md files</span>'
                         if not _mds else "") + "</div>",
                    unsafe_allow_html=True,
                )

            # Apps in the chosen projects that had no DocMDs match — surfaced
            # subtly so the user sees coverage gaps rather than silent misses.
            _missing = sorted({_d["application"] for _d in _detail
                               if not _d["folder"] and _d["application"]},
                              key=str.lower)
            if _missing:
                st.markdown(
                    '<div class="dc-doc-miss"><span class="dc-doc-miss-h">'
                    f'{len(_missing)} app'
                    f'{"s" if len(_missing) != 1 else ""} without docs</span>'
                    + "".join(f'<span class="dc-doc-miss-i">{html.escape(_a)}</span>'
                              for _a in _missing) + "</div>",
                    unsafe_allow_html=True,
                )

        # ── Conversation (scrollable, fixed height) ─────────────────────────
        with st.container(height=300, key="cc_docchat_msgs"):
            _render_messages()

        # ── Input ───────────────────────────────────────────────────────────
        _prompt = st.chat_input("Ask about your docs…", key="_dc_input")
        if _prompt:
            _sid = st.session_state["_dc_session_id"]
            _uname = st.session_state.get("username", "")
            _ctx_text, _doc_ids = _build_context(
                st.session_state["_dc_selected_apps"])

            _user_msg = {
                "role": "user", "content": _prompt,
                "timestamp": datetime.now().strftime("%H:%M"),
                "tokens": _estimate_tokens(_prompt),
            }
            st.session_state["_dc_messages"].append(_user_msg)
            db_save_message(_user_msg, _sid, _uname, _doc_ids)

            _api = [{"role": "system", "content": build_system_prompt(_ctx_text)}]
            _api += [{"role": _m["role"], "content": _m["content"]}
                     for _m in st.session_state["_dc_messages"]]

            with st.container(height=300, key="cc_docchat_msgs_live"):
                _render_messages()
                with st.chat_message("assistant"):
                    _ph = st.empty()
                    _full = ""
                    _t0 = time.time()
                    _is_err = False
                    try:
                        for _tok in chat_stream(_api):
                            _full += _tok
                            _ph.markdown(_full + "▌")
                        _ph.markdown(_full)
                    except Exception as _e:
                        _is_err = True
                        _full = (
                            "⚠ Couldn't reach the model "
                            f"({type(_e).__name__}). Check that Ollama at "
                            f"`{OLLAMA_URL}` is reachable."
                        )
                        _ph.error(_full)
            _asst = {
                "role": "assistant", "content": _full,
                "timestamp": datetime.now().strftime("%H:%M"),
                "duration": round(time.time() - _t0, 2),
                "tokens": _estimate_tokens(_full),
            }
            st.session_state["_dc_messages"].append(_asst)
            db_save_message(_asst, _sid, _uname, _doc_ids, has_error=_is_err)
            st.rerun(scope="fragment")

        # Footer controls
        _f1, _f2 = st.columns([1, 1])
        with _f1:
            if st.button("🗑 Clear", key="_dc_clear_btn",
                         use_container_width=True,
                         disabled=not st.session_state["_dc_messages"]):
                st.session_state["_dc_messages"] = []
                st.session_state["_dc_session_id"] = uuid.uuid4().hex
                st.rerun(scope="fragment")
        with _f2:
            st.markdown(
                f'<div class="dc-foot">logged · {len(st.session_state["_dc_messages"])} msgs</div>',
                unsafe_allow_html=True,
            )
