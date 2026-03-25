"""VM Log Explorer - lightweight, read-only log monitoring over SSH."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

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

# VMs to monitor - add/remove entries as needed.
# Each entry: display name -> vault secret path (passed to VaultClient).
VM_REGISTRY: Dict[str, str] = {
    "vm-asg-prod-01": "myvm",
    # "vm-asg-prod-02": "myvm2",
}

TAIL_LINES_OPTIONS = [50, 100, 200, 500, 1000]

_PLOTLY_TEMPLATE = "plotly_white"

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
_PAGE_CSS = """
<style>
/* ---- Log Explorer ---- */

.log-hero {
    padding: 1.6rem 1.8rem 1rem;
    border-radius: 20px;
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 55%, #0f4c75 100%);
    color: #e2e8f0;
    margin-bottom: 1.4rem;
    position: relative;
    overflow: hidden;
}
.log-hero::before {
    content: "";
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 85% 30%, rgba(56,189,248,0.12) 0%, transparent 60%),
                radial-gradient(circle at 15% 80%, rgba(99,102,241,0.10) 0%, transparent 55%);
    pointer-events: none;
}
.log-hero h2 {
    margin: 0 0 .3rem;
    font-size: 1.5rem;
    font-weight: 800;
    letter-spacing: .5px;
    background: linear-gradient(120deg, #38bdf8, #818cf8, #34d399);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}
.log-hero p {
    margin: 0;
    font-size: .82rem;
    color: #94a3b8;
    line-height: 1.4;
}

/* KPI row */
.log-kpi-row {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin-bottom: 1.2rem;
}
.log-kpi {
    flex: 1 1 160px;
    background: linear-gradient(155deg, #f8fafc, #eef3f9);
    border: 1px solid #d0dce8;
    border-radius: 16px;
    padding: .9rem 1rem;
    text-align: center;
    box-shadow: 0 4px 14px -6px rgba(11,99,214,.18);
    transition: transform .2s, box-shadow .2s;
}
.log-kpi:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px -8px rgba(11,99,214,.28);
}
.log-kpi-label {
    font-size: .65rem;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    color: #51658a;
}
.log-kpi-value {
    font-size: 1.5rem;
    font-weight: 800;
    line-height: 1.1;
    margin-top: 2px;
    background: linear-gradient(120deg, #0b63d6, #6c5ce7);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}

/* File table card */
.log-table-card {
    background: linear-gradient(155deg, #ffffff, #f7fafd);
    border: 1px solid #d0dce8;
    border-radius: 18px;
    padding: 1.1rem 1.2rem;
    margin-bottom: 1.2rem;
    box-shadow: 0 5px 20px -6px rgba(11,99,214,.16);
}
.log-table-card h3 {
    margin: 0 0 .7rem;
    font-size: 1rem;
    color: #0b2140;
}

/* Tail viewer - terminal look */
.log-tail-wrap {
    background: #0f172a;
    color: #e2e8f0;
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: .74rem;
    line-height: 1.55;
    padding: 1rem 1.2rem;
    border-radius: 14px;
    max-height: 520px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    border: 1px solid #1e3a5f;
    box-shadow: inset 0 2px 8px rgba(0,0,0,.35);
}

/* Search result snippet */
.log-search-hit {
    background: #f8fafc;
    border: 1px solid #d0dce8;
    border-radius: 12px;
    padding: .7rem .9rem;
    margin-bottom: .6rem;
    font-family: "JetBrains Mono", monospace;
    font-size: .73rem;
    line-height: 1.5;
    color: #334155;
    white-space: pre-wrap;
    word-break: break-all;
    box-shadow: 0 2px 8px -4px rgba(0,0,0,.08);
}
.log-search-hit .hl {
    background: #fef08a;
    color: #713f12;
    padding: 0 2px;
    border-radius: 3px;
    font-weight: 700;
}
</style>
"""

st.markdown(_PAGE_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data class
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


# ---------------------------------------------------------------------------
# SSH helpers (all read-only, lightweight)
# ---------------------------------------------------------------------------
def _get_credentials(vault_key: str) -> tuple:
    """Fetch SSH creds from Vault. Cached in session."""
    cache_key = f"_vault_{vault_key}"
    if cache_key not in st.session_state:
        from utils.vault import VaultClient

        vc = VaultClient()
        config = vc.read_all_nested_secrets(vault_key)
        st.session_state[cache_key] = (config["username"], config["password"])
    return st.session_state[cache_key]


def _ssh_exec(host: str, username: str, password: str, cmd: str, timeout: int = 15) -> str:
    """Open a short-lived SSH connection, run a read-only command, return stdout."""
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
    """List log files with size + timestamp. Uses maxdepth 3 to stay fast."""
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
        ts = parts[1]
        full_path = parts[2]
        idx = full_path.rfind("/")
        if idx == -1:
            continue
        results.append(LogFile(
            vm=vm,
            directory=full_path[: idx + 1],
            filename=full_path[idx + 1 :],
            size_bytes=size,
            modified=ts,
        ))
    return results


def _build_grep_cmd(pattern: str, files: List[str]) -> str:
    escaped = pattern.replace("'", "'\\''")
    file_list = " ".join(f'"{f}"' for f in files)
    return f"grep -inH --color=never -m 50 '{escaped}' {file_list} 2>/dev/null | head -n 200"


def _build_tail_cmd(filepath: str, lines: int = 100) -> str:
    return f"tail -n {lines} '{filepath}' 2>/dev/null"


# ---------------------------------------------------------------------------
# Cached inventory fetch
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_log_inventory(vm: str, vault_key: str) -> List[dict]:
    user, pwd = _get_credentials(vault_key)
    raw = _ssh_exec(vm, user, pwd, _build_find_cmd(), timeout=20)
    return [lf.__dict__ for lf in _parse_find_output(raw, vm)]


# ---------------------------------------------------------------------------
# Highlight helper
# ---------------------------------------------------------------------------
def _highlight(text: str, pattern: str) -> str:
    if not pattern:
        return text
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    try:
        escaped = re.escape(pattern)
        return re.sub(f"({escaped})", r'<span class="hl">\1</span>', safe, flags=re.IGNORECASE)
    except re.error:
        return safe


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
def _render_kpi_row(total_files: int, total_size: int, largest: Optional[LogFile], dir_count: int):
    size_label = LogFile(vm="", directory="", filename="", size_bytes=total_size, modified="").size_human
    largest_label = f"{largest.filename} ({largest.size_human})" if largest else "N/A"
    st.markdown(f"""
    <div class="log-kpi-row">
        <div class="log-kpi">
            <div class="log-kpi-label">Total Log Files</div>
            <div class="log-kpi-value">{total_files}</div>
        </div>
        <div class="log-kpi">
            <div class="log-kpi-label">Total Size</div>
            <div class="log-kpi-value">{size_label}</div>
        </div>
        <div class="log-kpi">
            <div class="log-kpi-label">Directories</div>
            <div class="log-kpi-value">{dir_count}</div>
        </div>
        <div class="log-kpi">
            <div class="log-kpi-label">Largest File</div>
            <div class="log-kpi-value" style="font-size:1rem;">{largest_label}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Plotly helper
# ---------------------------------------------------------------------------
def _style_fig(fig, *, height: int = 340):
    fig.update_layout(
        template=_PLOTLY_TEMPLATE,
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=13, color="#0f172a"),
        title=dict(x=0.02, xanchor="left", font=dict(size=15)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    try:
        fig.update_layout(colorway=px.colors.qualitative.Set2)
    except Exception:
        pass
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(15,23,42,0.08)", zeroline=False)
    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Hero banner
    st.markdown("""
    <div class="log-hero">
        <h2>Log Explorer</h2>
        <p>Lightweight, read-only log monitoring for Tibco ASG application servers.
        Browse log inventories, visualize file sizes, search contents, and tail files on demand.</p>
    </div>
    """, unsafe_allow_html=True)

    # VM selector
    vm_names = list(VM_REGISTRY.keys())
    if not vm_names:
        st.warning("No VMs configured. Add entries to `VM_REGISTRY` in this file.")
        return

    selected_vm = st.selectbox("Target VM", vm_names, index=0)
    vault_key = VM_REGISTRY[selected_vm]

    # Fetch inventory
    with st.spinner(f"Scanning log files on **{selected_vm}** ..."):
        try:
            raw_logs = fetch_log_inventory(selected_vm, vault_key)
        except Exception as exc:
            st.error(f"Failed to connect to {selected_vm}: {exc}")
            return

    if not raw_logs:
        st.info("No log files found in the configured directories.")
        return

    logs = [LogFile(**d) for d in raw_logs]

    # KPIs
    total_size = sum(lf.size_bytes for lf in logs)
    largest = max(logs, key=lambda lf: lf.size_bytes)
    dirs = {lf.directory for lf in logs}
    _render_kpi_row(len(logs), total_size, largest, len(dirs))

    # Tabs
    tab_browse, tab_charts, tab_search, tab_tail = st.tabs(
        ["Browse Files", "Size Analysis", "Search Logs", "Tail File"]
    )

    # ── Browse ───────────────────────────────────────────────────────
    with tab_browse:
        st.markdown('<div class="log-table-card">', unsafe_allow_html=True)
        st.markdown("### Log File Inventory")

        col_dir, col_ext, col_sort = st.columns([2, 1, 1])
        all_dirs = sorted({lf.directory for lf in logs})
        all_exts = sorted({lf.extension for lf in logs})

        with col_dir:
            sel_dirs = st.multiselect("Directory", all_dirs, default=all_dirs, key="browse_dirs")
        with col_ext:
            sel_exts = st.multiselect("Extension", all_exts, default=all_exts, key="browse_exts")
        with col_sort:
            sort_by = st.selectbox("Sort by", ["Size (desc)", "Size (asc)", "Name", "Modified"], key="browse_sort")

        filtered = [lf for lf in logs if lf.directory in sel_dirs and lf.extension in sel_exts]

        if sort_by == "Size (desc)":
            filtered.sort(key=lambda x: x.size_bytes, reverse=True)
        elif sort_by == "Size (asc)":
            filtered.sort(key=lambda x: x.size_bytes)
        elif sort_by == "Name":
            filtered.sort(key=lambda x: x.filename.lower())
        else:
            filtered.sort(key=lambda x: x.modified, reverse=True)

        if filtered:
            df = pd.DataFrame({
                "File": [f.filename for f in filtered],
                "Directory": [f.directory for f in filtered],
                "Size": [f.size_human for f in filtered],
                "Modified": [f.modified for f in filtered],
                "Ext": [f.extension for f in filtered],
            })
            st.dataframe(
                df,
                use_container_width=True,
                height=min(400, 40 + len(df) * 35),
                hide_index=True,
            )
            st.caption(f"Showing {len(filtered)} of {len(logs)} files")
        else:
            st.info("No files match the current filters.")

        st.markdown("</div>", unsafe_allow_html=True)

    # ── Charts ───────────────────────────────────────────────────────
    with tab_charts:
        st.markdown("### Size Analysis")

        # Treemap
        tree_data = [{
            "directory": lf.directory,
            "file": lf.filename,
            "size_mb": round(lf.size_bytes / (1024 * 1024), 3),
            "size_bytes": lf.size_bytes,
        } for lf in logs]
        tree_df = pd.DataFrame(tree_data)

        fig_tree = px.treemap(
            tree_df,
            path=["directory", "file"],
            values="size_bytes",
            title="Log File Size Treemap",
            color="size_mb",
            color_continuous_scale="Blues",
        )
        _style_fig(fig_tree, height=440)
        fig_tree.update_layout(margin=dict(l=5, r=5, t=50, b=5))
        st.plotly_chart(fig_tree, use_container_width=True)

        # Top 15 bar chart
        top_n = sorted(logs, key=lambda x: x.size_bytes, reverse=True)[:15]
        bar_df = pd.DataFrame({
            "file": [f.filename for f in top_n],
            "size_mb": [round(f.size_bytes / (1024 * 1024), 2) for f in top_n],
        })
        fig_bar = px.bar(
            bar_df, x="size_mb", y="file", orientation="h",
            title="Top 15 Largest Log Files (MB)",
            labels={"size_mb": "Size (MB)", "file": ""},
            color="size_mb", color_continuous_scale="Viridis",
        )
        _style_fig(fig_bar, height=380)
        fig_bar.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_bar, use_container_width=True)

        # Extension pie
        ext_sizes: Dict[str, int] = {}
        for lf in logs:
            ext_sizes[lf.extension or "(none)"] = ext_sizes.get(lf.extension or "(none)", 0) + lf.size_bytes
        pie_df = pd.DataFrame({"extension": list(ext_sizes.keys()), "size_bytes": list(ext_sizes.values())})
        fig_pie = px.pie(pie_df, names="extension", values="size_bytes", title="Size by Extension", hole=0.4)
        _style_fig(fig_pie, height=340)
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── Search ───────────────────────────────────────────────────────
    with tab_search:
        st.markdown("### Search Log Contents")
        st.caption("Runs a lightweight `grep` on the remote VM (max 200 result lines, 50 per file).")

        col_pat, col_scope = st.columns([2, 1])
        with col_pat:
            search_pattern = st.text_input("Search pattern", placeholder="ERROR|Exception|timeout", key="search_pat")
        with col_scope:
            scope_options = ["All files"] + sorted({lf.directory for lf in logs})
            search_scope = st.selectbox("Scope", scope_options, key="search_scope")

        if search_pattern:
            target_files = [
                lf.path for lf in logs
                if search_scope == "All files" or lf.directory == search_scope
            ]
            if not target_files:
                st.info("No files in the selected scope.")
            else:
                with st.spinner("Searching ..."):
                    try:
                        user, pwd = _get_credentials(vault_key)
                        raw_results = _ssh_exec(
                            selected_vm, user, pwd,
                            _build_grep_cmd(search_pattern, target_files),
                            timeout=20,
                        )
                    except Exception as exc:
                        st.error(f"Search failed: {exc}")
                        raw_results = ""

                if raw_results.strip():
                    lines = raw_results.strip().splitlines()
                    st.success(f"Found {len(lines)} matching line(s)")

                    grouped: Dict[str, List[str]] = {}
                    for line in lines:
                        colon_idx = line.find(":")
                        if colon_idx > 0:
                            grouped.setdefault(line[:colon_idx], []).append(line[colon_idx + 1:])
                        else:
                            grouped.setdefault("(unknown)", []).append(line)

                    for fpath, hits in grouped.items():
                        fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
                        with st.expander(f"{fname} \u2014 {len(hits)} hit(s)", expanded=len(grouped) <= 3):
                            st.caption(fpath)
                            for hit in hits:
                                st.markdown(
                                    f'<div class="log-search-hit">{_highlight(hit, search_pattern)}</div>',
                                    unsafe_allow_html=True,
                                )
                else:
                    st.info("No matches found.")

    # ── Tail ─────────────────────────────────────────────────────────
    with tab_tail:
        st.markdown("### Tail Log File")
        st.caption("Fetch the last N lines of a log file (read-only `tail`).")

        col_file, col_lines = st.columns([3, 1])
        file_paths = sorted({lf.path for lf in logs})

        with col_file:
            tail_file = st.selectbox("File", file_paths, key="tail_file")
        with col_lines:
            tail_lines = st.selectbox("Lines", TAIL_LINES_OPTIONS, index=1, key="tail_lines")

        do_tail = st.button("Fetch tail", type="primary")

        if do_tail and tail_file:
            with st.spinner(f"Tailing {tail_file} ({tail_lines} lines) ..."):
                try:
                    user, pwd = _get_credentials(vault_key)
                    tail_output = _ssh_exec(
                        selected_vm, user, pwd,
                        _build_tail_cmd(tail_file, tail_lines),
                    )
                except Exception as exc:
                    st.error(f"Tail failed: {exc}")
                    tail_output = ""

            if tail_output:
                hl_pattern = st.text_input("Highlight pattern (optional)", placeholder="ERROR|WARN", key="tail_hl")

                if hl_pattern:
                    rendered = "<br>".join(_highlight(line, hl_pattern) for line in tail_output.splitlines())
                else:
                    rendered = (
                        tail_output
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )

                st.markdown(f'<div class="log-tail-wrap">{rendered}</div>', unsafe_allow_html=True)

                st.download_button(
                    label="Download tail output",
                    data=tail_output,
                    file_name=f"tail_{tail_file.rsplit('/', 1)[-1]}",
                    mime="text/plain",
                )
            else:
                st.info("File is empty or could not be read.")

    # Refresh
    st.markdown("---")
    col_r1, col_r2 = st.columns([1, 4])
    with col_r1:
        if st.button("Refresh inventory", use_container_width=True):
            fetch_log_inventory.clear()
            st.rerun()
    with col_r2:
        st.caption("Inventory cached for 2 min. Click refresh to force a rescan.")


main()
