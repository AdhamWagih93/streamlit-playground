"""VM Log Explorer - lightweight, read-only log monitoring across multiple VMs."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import paramiko
import plotly.express as px
import streamlit as st

from src.theme import set_theme

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
set_theme(page_title="Log Explorer", page_icon="\U0001f4dc")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOG_DIRS = [
    "/apps/tibco/asg/2.3/bin/",
    "/apps/tibco/asg/2.5/bin/",
]

LOG_EXTENSIONS = ("*.log", "*.out", "*.err", "*.trace", "*.txt")

# VMs: display name -> vault secret path
VM_REGISTRY: Dict[str, str] = {
    "vm-asg-prod-01": "myvm",
    # "vm-asg-prod-02": "myvm2",
}

TAIL_LINES_OPTIONS = [50, 100, 200, 500, 1000]
_PLOTLY_TPL = "plotly_white"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* ---- Log Explorer ---- */

/* Compact header bar */
.log-header {
    display: flex; align-items: center; gap: 14px;
    padding: .7rem 1.1rem;
    border-radius: 14px;
    background: linear-gradient(135deg, #0f172a, #1e3a5f);
    margin-bottom: .9rem;
}
.log-header-icon {
    font-size: 1.6rem; line-height: 1;
}
.log-header-text h2 {
    margin: 0; font-size: 1.15rem; font-weight: 800; letter-spacing: .4px;
    background: linear-gradient(120deg, #38bdf8, #818cf8, #34d399);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
.log-header-text p {
    margin: 0; font-size: .7rem; color: #94a3b8; line-height: 1.3;
}

/* KPI strip */
.lx-kpi-strip {
    display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: .9rem;
}
.lx-kpi {
    flex: 1 1 120px;
    background: linear-gradient(155deg, #f8fafc, #eef3f9);
    border: 1px solid #d0dce8; border-radius: 12px;
    padding: .55rem .7rem; text-align: center;
    box-shadow: 0 3px 10px -4px rgba(11,99,214,.15);
    transition: transform .18s, box-shadow .18s;
}
.lx-kpi:hover { transform: translateY(-1px); box-shadow: 0 6px 18px -6px rgba(11,99,214,.22); }
.lx-kpi-label {
    font-size: .58rem; font-weight: 700; letter-spacing: .5px;
    text-transform: uppercase; color: #51658a;
}
.lx-kpi-value {
    font-size: 1.25rem; font-weight: 800; line-height: 1.1; margin-top: 1px;
    background: linear-gradient(120deg, #0b63d6, #6c5ce7);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}

/* VM health cards */
.lx-vm-grid { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: .9rem; }
.lx-vm-card {
    flex: 1 1 200px;
    background: linear-gradient(155deg, #fff, #f7fafd);
    border: 1px solid #d0dce8; border-radius: 14px;
    padding: .65rem .85rem;
    box-shadow: 0 3px 12px -4px rgba(11,99,214,.12);
    transition: transform .18s, box-shadow .18s;
    cursor: default;
}
.lx-vm-card:hover { transform: translateY(-1px); box-shadow: 0 6px 20px -6px rgba(11,99,214,.22); }
.lx-vm-name {
    font-size: .78rem; font-weight: 700; color: #0b2140;
    display: flex; align-items: center; gap: 6px;
}
.lx-vm-dot {
    width: 7px; height: 7px; border-radius: 50%; display: inline-block;
    flex-shrink: 0;
}
.lx-vm-dot-ok { background: #22c55e; box-shadow: 0 0 4px rgba(34,197,94,.5); }
.lx-vm-dot-err { background: #ef4444; box-shadow: 0 0 4px rgba(239,68,68,.5); }
.lx-vm-stats {
    display: flex; gap: 12px; margin-top: 4px;
    font-size: .65rem; color: #64748b;
}
.lx-vm-stats span { white-space: nowrap; }
.lx-vm-stats strong { color: #334155; }

/* Terminal tail */
.log-tail-wrap {
    background: #0f172a; color: #e2e8f0;
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: .72rem; line-height: 1.5;
    padding: .8rem 1rem; border-radius: 12px;
    max-height: 480px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all;
    border: 1px solid #1e3a5f;
    box-shadow: inset 0 2px 6px rgba(0,0,0,.3);
}

/* Search hits */
.log-search-hit {
    background: #f8fafc; border: 1px solid #d0dce8; border-radius: 10px;
    padding: .5rem .7rem; margin-bottom: .45rem;
    font-family: "JetBrains Mono", monospace;
    font-size: .7rem; line-height: 1.45; color: #334155;
    white-space: pre-wrap; word-break: break-all;
    box-shadow: 0 1px 5px -2px rgba(0,0,0,.06);
}
.log-search-hit .hl {
    background: #fef08a; color: #713f12;
    padding: 0 2px; border-radius: 3px; font-weight: 700;
}

/* VM badge in search results */
.lx-vm-badge {
    display: inline-block;
    background: linear-gradient(120deg, #0b63d6, #6c5ce7);
    color: #fff; font-size: .55rem; font-weight: 700;
    padding: 1px 7px; border-radius: 10px; letter-spacing: .4px;
    margin-right: 4px; vertical-align: middle;
}

/* Compact popover trigger */
.lx-info-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 16px; height: 16px; border-radius: 50%;
    background: #e2e8f0; color: #475569;
    font-size: .6rem; font-weight: 800; cursor: help;
    vertical-align: middle; margin-left: 4px;
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class LogFile:
    vm: str
    directory: str
    filename: str
    size_bytes: int
    modified: str

    @property
    def path(self) -> str:
        return f"{self.directory.rstrip('/')}/{self.filename}"

    @property
    def size_human(self) -> str:
        b = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    @property
    def extension(self) -> str:
        parts = self.filename.rsplit(".", 1)
        return f".{parts[1]}" if len(parts) == 2 else ""


def _fmt_size(size_bytes: int) -> str:
    b = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ---------------------------------------------------------------------------
# SSH (read-only, lightweight)
# ---------------------------------------------------------------------------
def _get_credentials(vault_key: str) -> Tuple[str, str]:
    cache_key = f"_vault_{vault_key}"
    if cache_key not in st.session_state:
        from utils.vault import VaultClient
        vc = VaultClient()
        config = vc.read_all_nested_secrets(vault_key)
        st.session_state[cache_key] = (config["username"], config["password"])
    return st.session_state[cache_key]


def _ssh_exec(host: str, username: str, password: str, cmd: str, timeout: int = 15) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password, timeout=8)
        _, stdout, _ = client.exec_command(cmd, timeout=timeout)
        return stdout.read().decode("utf-8", errors="replace")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Remote commands (read-only, bounded)
# ---------------------------------------------------------------------------
def _build_find_cmd() -> str:
    ext_filters = " -o ".join(f'-name "{e}"' for e in LOG_EXTENSIONS)
    dir_list = " ".join(f'"{d}"' for d in LOG_DIRS)
    return (
        f"find {dir_list} -maxdepth 3 -type f \\( {ext_filters} \\) "
        f"-printf '%s\\t%TY-%Tm-%Td %TH:%TM\\t%p\\n' 2>/dev/null | sort -t$'\\t' -k3"
    )


def _parse_find_output(raw: str, vm: str) -> List[LogFile]:
    results: List[LogFile] = []
    for line in raw.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            size = int(parts[0])
        except ValueError:
            continue
        full_path = parts[2]
        idx = full_path.rfind("/")
        if idx == -1:
            continue
        results.append(LogFile(
            vm=vm, directory=full_path[:idx + 1],
            filename=full_path[idx + 1:],
            size_bytes=size, modified=parts[1],
        ))
    return results


def _build_grep_cmd(pattern: str, files: List[str]) -> str:
    escaped = pattern.replace("'", "'\\''")
    file_list = " ".join(f'"{f}"' for f in files)
    return f"grep -inH --color=never -m 50 '{escaped}' {file_list} 2>/dev/null | head -n 200"


def _build_tail_cmd(filepath: str, lines: int = 100) -> str:
    return f"tail -n {lines} '{filepath}' 2>/dev/null"


# ---------------------------------------------------------------------------
# Cached multi-VM inventory
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def _fetch_single_vm(vm: str, vault_key: str) -> Tuple[str, List[dict], Optional[str]]:
    """Returns (vm_name, log_dicts, error_or_None)."""
    try:
        user, pwd = _get_credentials(vault_key)
        raw = _ssh_exec(vm, user, pwd, _build_find_cmd(), timeout=20)
        return vm, [lf.__dict__ for lf in _parse_find_output(raw, vm)], None
    except Exception as exc:
        return vm, [], str(exc)


def fetch_all_inventories() -> Tuple[List[LogFile], Dict[str, Optional[str]]]:
    """Fetch inventory from all VMs in parallel. Returns (all_logs, {vm: error})."""
    all_logs: List[LogFile] = []
    vm_errors: Dict[str, Optional[str]] = {}

    # Parallel SSH to all VMs
    with ThreadPoolExecutor(max_workers=min(len(VM_REGISTRY), 5)) as pool:
        futures = {
            pool.submit(_fetch_single_vm, vm, vkey): vm
            for vm, vkey in VM_REGISTRY.items()
        }
        for fut in as_completed(futures):
            vm_name, log_dicts, err = fut.result()
            vm_errors[vm_name] = err
            all_logs.extend(LogFile(**d) for d in log_dicts)

    return all_logs, vm_errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _highlight(text: str, pattern: str) -> str:
    if not pattern:
        return text
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    try:
        return re.sub(f"({re.escape(pattern)})", r'<span class="hl">\1</span>', safe, flags=re.IGNORECASE)
    except re.error:
        return safe


def _style_fig(fig, *, height: int = 320):
    fig.update_layout(
        template=_PLOTLY_TPL, height=height,
        margin=dict(l=8, r=8, t=44, b=8),
        font=dict(family="Inter, Segoe UI, sans-serif", size=12, color="#0f172a"),
        title=dict(x=0.02, xanchor="left", font=dict(size=14)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    try:
        fig.update_layout(colorway=px.colors.qualitative.Set2)
    except Exception:
        pass
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(15,23,42,0.07)", zeroline=False)
    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # ── Header (compact) ─────────────────────────────────────────────
    st.markdown("""
    <div class="log-header">
        <div class="log-header-icon">\U0001f4dc</div>
        <div class="log-header-text">
            <h2>Log Explorer</h2>
            <p>Read-only log monitoring across Tibco ASG servers</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not VM_REGISTRY:
        st.warning("No VMs configured. Add entries to `VM_REGISTRY`.")
        return

    # ── Fetch all VMs in parallel ────────────────────────────────────
    with st.spinner("Scanning all VMs ..."):
        all_logs, vm_errors = fetch_all_inventories()

    # ── VM health overview cards ─────────────────────────────────────
    vm_log_map: Dict[str, List[LogFile]] = {}
    for lf in all_logs:
        vm_log_map.setdefault(lf.vm, []).append(lf)

    cards_html = '<div class="lx-vm-grid">'
    for vm in VM_REGISTRY:
        err = vm_errors.get(vm)
        vm_logs = vm_log_map.get(vm, [])
        dot_cls = "lx-vm-dot-err" if err else "lx-vm-dot-ok"
        file_count = len(vm_logs)
        total = sum(l.size_bytes for l in vm_logs)
        status_text = f'<span style="color:#ef4444;font-size:.62rem;">{err[:40]}...</span>' if err else ""
        cards_html += f"""
        <div class="lx-vm-card">
            <div class="lx-vm-name"><span class="lx-vm-dot {dot_cls}"></span>{vm}</div>
            {status_text}
            <div class="lx-vm-stats">
                <span><strong>{file_count}</strong> files</span>
                <span><strong>{_fmt_size(total)}</strong></span>
                <span><strong>{len({l.directory for l in vm_logs})}</strong> dirs</span>
            </div>
        </div>"""
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)

    if not all_logs:
        st.info("No log files found across any VM.")
        return

    # ── Aggregate KPIs ───────────────────────────────────────────────
    total_size = sum(l.size_bytes for l in all_logs)
    largest = max(all_logs, key=lambda l: l.size_bytes)
    all_dirs = {l.directory for l in all_logs}
    all_exts = sorted({l.extension for l in all_logs})
    reachable = sum(1 for e in vm_errors.values() if e is None)

    st.markdown(f"""
    <div class="lx-kpi-strip">
        <div class="lx-kpi"><div class="lx-kpi-label">VMs Online</div><div class="lx-kpi-value">{reachable}/{len(VM_REGISTRY)}</div></div>
        <div class="lx-kpi"><div class="lx-kpi-label">Total Files</div><div class="lx-kpi-value">{len(all_logs)}</div></div>
        <div class="lx-kpi"><div class="lx-kpi-label">Total Size</div><div class="lx-kpi-value">{_fmt_size(total_size)}</div></div>
        <div class="lx-kpi"><div class="lx-kpi-label">Directories</div><div class="lx-kpi-value">{len(all_dirs)}</div></div>
        <div class="lx-kpi"><div class="lx-kpi-label">Largest</div><div class="lx-kpi-value" style="font-size:.85rem">{largest.filename}</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ── Tabs ─────────────────────────────────────────────────────────
    tab_browse, tab_charts, tab_search, tab_tail = st.tabs([
        "\U0001f4c2 Browse", "\U0001f4ca Analysis", "\U0001f50d Search", "\U0001f4df Tail",
    ])

    # ── Browse ───────────────────────────────────────────────────────
    with tab_browse:
        # Compact filter bar in columns
        c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1])
        with c1:
            sel_vms = st.multiselect("VM", sorted(VM_REGISTRY.keys()), default=sorted(VM_REGISTRY.keys()), key="b_vm")
        with c2:
            sel_dirs = st.multiselect("Directory", sorted(all_dirs), default=sorted(all_dirs), key="b_dir")
        with c3:
            sel_exts = st.multiselect("Extension", all_exts, default=all_exts, key="b_ext")
        with c4:
            sort_by = st.selectbox("Sort", ["Size \u2193", "Size \u2191", "Name", "Modified \u2193"], key="b_sort")

        filtered = [
            l for l in all_logs
            if l.vm in sel_vms and l.directory in sel_dirs and l.extension in sel_exts
        ]

        if sort_by == "Size \u2193":
            filtered.sort(key=lambda x: x.size_bytes, reverse=True)
        elif sort_by == "Size \u2191":
            filtered.sort(key=lambda x: x.size_bytes)
        elif sort_by == "Name":
            filtered.sort(key=lambda x: x.filename.lower())
        else:
            filtered.sort(key=lambda x: x.modified, reverse=True)

        if filtered:
            df = pd.DataFrame({
                "VM": [f.vm for f in filtered],
                "File": [f.filename for f in filtered],
                "Directory": [f.directory for f in filtered],
                "Size": [f.size_human for f in filtered],
                "Bytes": [f.size_bytes for f in filtered],
                "Modified": [f.modified for f in filtered],
                "Ext": [f.extension for f in filtered],
            })

            st.dataframe(
                df[["VM", "File", "Directory", "Size", "Modified", "Ext"]],
                use_container_width=True,
                height=min(420, 38 + len(df) * 35),
                hide_index=True,
                column_config={
                    "VM": st.column_config.TextColumn(width="small"),
                    "File": st.column_config.TextColumn(width="medium"),
                    "Size": st.column_config.TextColumn(width="small"),
                    "Modified": st.column_config.TextColumn(width="small"),
                    "Ext": st.column_config.TextColumn(width="small"),
                },
            )
            st.caption(f"{len(filtered)} / {len(all_logs)} files")
        else:
            st.info("No files match filters.")

    # ── Analysis ─────────────────────────────────────────────────────
    with tab_charts:
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            # Treemap: VM > dir > file
            tree_df = pd.DataFrame([{
                "vm": l.vm, "directory": l.directory, "file": l.filename,
                "size_mb": round(l.size_bytes / 1048576, 3), "size_bytes": l.size_bytes,
            } for l in all_logs])
            fig_tree = px.treemap(
                tree_df, path=["vm", "directory", "file"], values="size_bytes",
                title="Size Treemap", color="size_mb", color_continuous_scale="Blues",
            )
            _style_fig(fig_tree, height=400)
            fig_tree.update_layout(margin=dict(l=4, r=4, t=44, b=4))
            st.plotly_chart(fig_tree, use_container_width=True)

        with chart_col2:
            # Size per VM stacked bar
            vm_ext_data = []
            for l in all_logs:
                vm_ext_data.append({"VM": l.vm, "Extension": l.extension or "(none)", "MB": l.size_bytes / 1048576})
            vm_ext_df = pd.DataFrame(vm_ext_data).groupby(["VM", "Extension"], as_index=False)["MB"].sum()
            fig_stack = px.bar(
                vm_ext_df, x="VM", y="MB", color="Extension",
                title="Size per VM by Extension",
            )
            _style_fig(fig_stack, height=400)
            st.plotly_chart(fig_stack, use_container_width=True)

        # Top files bar (full width, compact)
        top_n = sorted(all_logs, key=lambda x: x.size_bytes, reverse=True)[:12]
        bar_df = pd.DataFrame({
            "label": [f"{f.vm}:{f.filename}" for f in top_n],
            "MB": [round(f.size_bytes / 1048576, 2) for f in top_n],
        })
        fig_bar = px.bar(
            bar_df, x="MB", y="label", orientation="h",
            title="Top 12 Largest Files",
            color="MB", color_continuous_scale="Viridis",
        )
        _style_fig(fig_bar, height=320)
        fig_bar.update_layout(yaxis=dict(autorange="reversed"), showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── Search (cross-VM) ────────────────────────────────────────────
    with tab_search:
        s_col1, s_col2, s_col3 = st.columns([2.5, 1.2, 1])
        with s_col1:
            search_pattern = st.text_input(
                "Pattern", placeholder="ERROR|Exception|OOM|timeout",
                key="s_pat", label_visibility="collapsed",
            )
        with s_col2:
            scope_opts = ["All directories"] + sorted(all_dirs)
            search_scope = st.selectbox("Scope", scope_opts, key="s_scope", label_visibility="collapsed")
        with s_col3:
            search_vms = st.multiselect("VMs", sorted(VM_REGISTRY.keys()), default=sorted(VM_REGISTRY.keys()), key="s_vm", label_visibility="collapsed")

        with st.popover("\u2139\ufe0f How search works"):
            st.markdown(
                "Runs `grep -inH -m 50` per file on each VM, capped at **200 lines** total per VM. "
                "Pattern is case-insensitive. Results are streamed per VM in parallel.",
                unsafe_allow_html=False,
            )

        if search_pattern and search_vms:
            # Build per-VM file lists
            vm_file_map: Dict[str, List[str]] = {}
            for lf in all_logs:
                if lf.vm not in search_vms:
                    continue
                if search_scope != "All directories" and lf.directory != search_scope:
                    continue
                vm_file_map.setdefault(lf.vm, []).append(lf.path)

            if not vm_file_map:
                st.info("No files in scope.")
            else:
                with st.spinner("Searching across VMs ..."):
                    all_results: Dict[str, str] = {}
                    with ThreadPoolExecutor(max_workers=min(len(vm_file_map), 5)) as pool:
                        futs = {}
                        for vm, files in vm_file_map.items():
                            vkey = VM_REGISTRY[vm]
                            user, pwd = _get_credentials(vkey)
                            cmd = _build_grep_cmd(search_pattern, files)
                            futs[pool.submit(_ssh_exec, vm, user, pwd, cmd, 20)] = vm
                        for fut in as_completed(futs):
                            vm = futs[fut]
                            try:
                                all_results[vm] = fut.result()
                            except Exception as exc:
                                all_results[vm] = f"[ERROR] {exc}"

                # Render grouped results
                total_hits = 0
                for vm in sorted(all_results):
                    raw = all_results[vm]
                    if not raw.strip() or raw.startswith("[ERROR]"):
                        continue
                    lines = raw.strip().splitlines()
                    total_hits += len(lines)

                    # Group by file
                    grouped: Dict[str, List[str]] = {}
                    for line in lines:
                        ci = line.find(":")
                        if ci > 0:
                            grouped.setdefault(line[:ci], []).append(line[ci + 1:])
                        else:
                            grouped.setdefault("(unknown)", []).append(line)

                    for fpath, hits in grouped.items():
                        fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
                        with st.expander(f"{vm} \u2022 {fname} \u2014 {len(hits)} hit(s)"):
                            st.caption(fpath)
                            for hit in hits[:30]:  # cap display per file
                                st.markdown(
                                    f'<div class="log-search-hit">{_highlight(hit, search_pattern)}</div>',
                                    unsafe_allow_html=True,
                                )
                            if len(hits) > 30:
                                st.caption(f"... and {len(hits) - 30} more lines")

                if total_hits:
                    st.success(f"{total_hits} total match(es) across {len([v for v in all_results.values() if v.strip() and not v.startswith('[ERROR]')])} VM(s)")
                else:
                    st.info("No matches found.")

                # Show errors
                for vm, raw in all_results.items():
                    if raw.startswith("[ERROR]"):
                        st.warning(f"{vm}: {raw}")

    # ── Tail ─────────────────────────────────────────────────────────
    with tab_tail:
        t_c1, t_c2, t_c3 = st.columns([1, 2.5, .8])
        with t_c1:
            tail_vm = st.selectbox("VM", sorted(VM_REGISTRY.keys()), key="t_vm")
        with t_c2:
            vm_files = sorted({l.path for l in all_logs if l.vm == tail_vm})
            tail_file = st.selectbox("File", vm_files if vm_files else ["(no files)"], key="t_file")
        with t_c3:
            tail_lines = st.selectbox("Lines", TAIL_LINES_OPTIONS, index=1, key="t_lines")

        tc1, tc2 = st.columns([1, 5])
        with tc1:
            do_tail = st.button("Fetch", type="primary", use_container_width=True)
        with tc2:
            with st.popover("\u2699\ufe0f Options"):
                hl_pattern = st.text_input("Highlight pattern", placeholder="ERROR|WARN", key="t_hl")
                auto_scroll = st.toggle("Auto-scroll to bottom", value=True, key="t_scroll")

        if do_tail and tail_file and tail_file != "(no files)":
            with st.spinner(f"Tailing ..."):
                try:
                    vkey = VM_REGISTRY[tail_vm]
                    user, pwd = _get_credentials(vkey)
                    tail_output = _ssh_exec(tail_vm, user, pwd, _build_tail_cmd(tail_file, tail_lines))
                except Exception as exc:
                    st.error(f"Tail failed: {exc}")
                    tail_output = ""

            if tail_output:
                if hl_pattern:
                    rendered = "<br>".join(_highlight(ln, hl_pattern) for ln in tail_output.splitlines())
                else:
                    rendered = (tail_output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

                st.markdown(f'<div class="log-tail-wrap">{rendered}</div>', unsafe_allow_html=True)

                dl_c1, dl_c2 = st.columns([1, 5])
                with dl_c1:
                    st.download_button(
                        "Download", data=tail_output,
                        file_name=f"tail_{tail_file.rsplit('/', 1)[-1]}",
                        mime="text/plain", use_container_width=True,
                    )
            else:
                st.info("Empty or unreadable file.")

    # ── Footer refresh ───────────────────────────────────────────────
    st.divider()
    fc1, fc2 = st.columns([1, 5])
    with fc1:
        if st.button("Refresh", use_container_width=True, help="Clear cache and rescan all VMs"):
            _fetch_single_vm.clear()
            st.rerun()
    with fc2:
        st.caption("Inventory cached 2 min. All commands are read-only (`find`, `grep -m`, `tail`).")


main()
