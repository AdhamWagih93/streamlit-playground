from typing import Any, Dict, List

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.theme import set_theme


set_theme(page_title="Trivy Scanner", page_icon="🔒")

admin = load_admin_config()
if not admin.is_mcp_enabled("trivy", default=True):
    st.info("Trivy MCP is disabled by Admin.")
    st.stop()

# Add status badge styles
add_mcp_status_styles()

# Modern styling
st.markdown(
    """
    <style>
    .trivy-hero {
        background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(124, 58, 237, 0.3);
    }
    .trivy-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .trivy-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .trivy-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .trivy-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1e293b;
    }
    .severity-critical {
        background: #dc2626;
        color: white;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .severity-high {
        background: #f97316;
        color: white;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .severity-medium {
        background: #eab308;
        color: black;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .severity-low {
        background: #22c55e;
        color: white;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .severity-unknown {
        background: #6b7280;
        color: white;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    .metric-box {
        background: #f1f5f9;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
        border: 1px solid #e2e8f0;
    }
    .metric-box-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0f172a;
    }
    .metric-box-label {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 0.25rem;
    }
    .vuln-table {
        width: 100%;
        border-collapse: collapse;
    }
    .vuln-table th, .vuln-table td {
        padding: 0.5rem;
        text-align: left;
        border-bottom: 1px solid #e2e8f0;
    }
    .vuln-table th {
        background: #f1f5f9;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <div class="trivy-hero">
        <h1>Trivy Security Scanner</h1>
        <p>Comprehensive vulnerability scanning for containers, filesystems, and IaC</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _get_trivy_client(force_new: bool = False):
    """Get the Trivy MCP client."""
    return get_mcp_client("trivy", force_new=force_new)


def _get_trivy_tools(force_reload: bool = False) -> List[Dict[str, Any]]:
    """Get Trivy MCP tools using the unified client."""
    client = _get_trivy_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_trivy_tools"] = tools
    st.session_state["_trivy_tools_sig"] = get_server_url("trivy")
    return tools


def _invoke(tools, name: str, args: Dict[str, Any]) -> Any:
    """Invoke a Trivy MCP tool."""
    client = _get_trivy_client()
    return client.invoke(name, args)


def _count_vulnerabilities(data: Dict[str, Any]) -> Dict[str, int]:
    """Count vulnerabilities by severity from Trivy scan results."""
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}

    results = data.get("Results", [])
    for result in results:
        vulnerabilities = result.get("Vulnerabilities", [])
        for vuln in vulnerabilities:
            severity = vuln.get("Severity", "UNKNOWN").upper()
            if severity in counts:
                counts[severity] += 1
            else:
                counts["UNKNOWN"] += 1

    return counts


def _extract_vulnerabilities(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract vulnerability details from Trivy scan results."""
    vulns = []

    results = data.get("Results", [])
    for result in results:
        target = result.get("Target", "Unknown")
        target_type = result.get("Type", "")
        vulnerabilities = result.get("Vulnerabilities", [])

        for vuln in vulnerabilities:
            vulns.append({
                "target": target,
                "type": target_type,
                "id": vuln.get("VulnerabilityID", ""),
                "pkg": vuln.get("PkgName", ""),
                "installed": vuln.get("InstalledVersion", ""),
                "fixed": vuln.get("FixedVersion", ""),
                "severity": vuln.get("Severity", "UNKNOWN"),
                "title": vuln.get("Title", ""),
                "description": vuln.get("Description", "")[:200] + "..." if len(vuln.get("Description", "")) > 200 else vuln.get("Description", ""),
            })

    return vulns


# Connection status info
st.subheader("Connection Status")

trivy_url = get_server_url("trivy")

# Invalidate cached tools if the target URL changes
if st.session_state.get("_trivy_tools_sig") != trivy_url:
    st.session_state.pop("_trivy_tools", None)
    st.session_state["_trivy_tools_sig"] = trivy_url

st.markdown(
    f"""
    <div class="trivy-card" style="padding: 1rem;">
        <div style="color: #64748b;">Transport: <strong>streamable-http</strong> &nbsp;|&nbsp; URL: <code>{trivy_url}</code></div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()

# Sidebar controls
with st.sidebar:
    st.markdown("### Controls")

    if "trivy_auto_load_tools" not in st.session_state:
        st.session_state.trivy_auto_load_tools = False

    st.session_state.trivy_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.trivy_auto_load_tools),
        help="When enabled, the page will discover tools automatically on open.",
    )

    load_clicked = st.button("Load/refresh tools", use_container_width=True)

    st.divider()

    st.markdown("### Scan Options")

    severity_options = st.multiselect(
        "Severities to report",
        options=["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"],
        default=["CRITICAL", "HIGH", "MEDIUM"],
        help="Select which severity levels to include in scan results",
    )
    st.session_state["_trivy_severity"] = ",".join(severity_options) if severity_options else "CRITICAL,HIGH,MEDIUM,LOW"

    st.session_state["_trivy_ignore_unfixed"] = st.checkbox(
        "Ignore unfixed vulnerabilities",
        value=st.session_state.get("_trivy_ignore_unfixed", False),
        help="Only show vulnerabilities that have fixes available",
    )

    st.session_state["_trivy_skip_db_update"] = st.checkbox(
        "Skip DB update",
        value=st.session_state.get("_trivy_skip_db_update", False),
        help="Skip vulnerability database update (faster but may miss new vulns)",
    )

    st.divider()

    st.markdown("### Last Scan Stats")
    last_scan = st.session_state.get("_trivy_last_scan", {})
    if last_scan:
        counts = _count_vulnerabilities(last_scan)
        st.metric("Critical", counts["CRITICAL"])
        st.metric("High", counts["HIGH"])
        st.metric("Medium", counts["MEDIUM"])
        st.metric("Low", counts["LOW"])

# Tool loading
should_load = bool(load_clicked) or (
    bool(st.session_state.get("trivy_auto_load_tools")) and "_trivy_tools" not in st.session_state
)

if should_load:
    try:
        with st.spinner("Loading Trivy MCP tools..."):
            _get_trivy_tools(force_reload=bool(load_clicked))
            st.success("Tools loaded successfully")
    except Exception as exc:
        st.error(f"Failed to load Trivy MCP tools: {exc}")
        st.info(
            "**Troubleshooting:**\n"
            "- Ensure trivy is installed and in your PATH\n"
            "- For remote connections, verify TRIVY_MCP_URL\n"
            "- Check `trivy --version` in your terminal"
        )

tools = st.session_state.get("_trivy_tools")
if not tools:
    st.info("Trivy tools are not loaded yet. Click **Load/refresh tools** in the sidebar to begin.")
    st.stop()

# Main content tabs
tabs = st.tabs(["Image Scan", "Filesystem Scan", "Repository Scan", "IaC Scan", "SBOM", "Tools & Debug"])

# --- IMAGE SCAN TAB ---
with tabs[0]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Container Image Vulnerability Scan")

    st.markdown("Scan Docker/OCI container images for known vulnerabilities.")

    image_ref = st.text_input(
        "Image reference",
        value="alpine:latest",
        placeholder="e.g., nginx:latest, python:3.11-slim, registry/image:tag",
        help="Container image to scan",
    )

    col_opts1, col_opts2 = st.columns(2)
    with col_opts1:
        vuln_type = st.selectbox(
            "Vulnerability type",
            options=["All", "os", "library"],
            index=0,
            help="Type of vulnerabilities to scan for",
        )
    with col_opts2:
        scanners = st.multiselect(
            "Scanners",
            options=["vuln", "secret", "misconfig"],
            default=["vuln"],
            help="Types of issues to scan for",
        )

    if st.button("Scan Image", use_container_width=True, type="primary"):
        if image_ref.strip():
            with st.spinner(f"Scanning {image_ref}... (this may take a few minutes)"):
                args = {
                    "image": image_ref.strip(),
                    "severity": st.session_state.get("_trivy_severity", "CRITICAL,HIGH,MEDIUM,LOW"),
                    "ignore_unfixed": st.session_state.get("_trivy_ignore_unfixed", False),
                    "skip_db_update": st.session_state.get("_trivy_skip_db_update", False),
                }
                if vuln_type != "All":
                    args["vuln_type"] = vuln_type
                if scanners:
                    args["scanners"] = ",".join(scanners)

                result = _invoke(tools, "trivy_scan_image", args)

                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_trivy_last_scan"] = result.get("data", {})
                    st.session_state["_trivy_last_scan_type"] = "image"
                    st.success("Scan completed!")
                else:
                    st.error(f"Scan failed: {result}")
        else:
            st.warning("Please enter an image reference")

    # Display results
    if st.session_state.get("_trivy_last_scan_type") == "image":
        scan_data = st.session_state.get("_trivy_last_scan", {})
        if scan_data:
            counts = _count_vulnerabilities(scan_data)
            total = sum(counts.values())

            st.markdown(
                f"""
                <div class="metric-grid">
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #dc2626;">{counts['CRITICAL']}</div>
                        <div class="metric-box-label">Critical</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #f97316;">{counts['HIGH']}</div>
                        <div class="metric-box-label">High</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #eab308;">{counts['MEDIUM']}</div>
                        <div class="metric-box-label">Medium</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value" style="color: #22c55e;">{counts['LOW']}</div>
                        <div class="metric-box-label">Low</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-box-value">{total}</div>
                        <div class="metric-box-label">Total</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            vulns = _extract_vulnerabilities(scan_data)
            if vulns:
                st.markdown("#### Vulnerabilities Found")

                # Filter by severity
                severity_filter = st.multiselect(
                    "Filter by severity",
                    options=["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                    default=["CRITICAL", "HIGH"],
                    key="image_vuln_filter",
                )

                filtered_vulns = [v for v in vulns if v["severity"] in severity_filter]

                for vuln in filtered_vulns[:50]:  # Limit display
                    severity_class = f"severity-{vuln['severity'].lower()}"
                    with st.expander(f"{vuln['id']} - {vuln['pkg']}", expanded=False):
                        st.markdown(f"<span class='{severity_class}'>{vuln['severity']}</span>", unsafe_allow_html=True)
                        st.markdown(f"**Package:** {vuln['pkg']} ({vuln['installed']})")
                        if vuln['fixed']:
                            st.markdown(f"**Fixed in:** {vuln['fixed']}")
                        st.markdown(f"**Target:** {vuln['target']}")
                        if vuln['title']:
                            st.markdown(f"**Title:** {vuln['title']}")
                        if vuln['description']:
                            st.markdown(f"**Description:** {vuln['description']}")

                if len(filtered_vulns) > 50:
                    st.caption(f"Showing 50 of {len(filtered_vulns)} vulnerabilities")

            with st.expander("Raw JSON Results", expanded=False):
                st.json(scan_data)

    st.markdown('</div>', unsafe_allow_html=True)

# --- FILESYSTEM SCAN TAB ---
with tabs[1]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Filesystem Vulnerability Scan")

    st.markdown("Scan local directories for vulnerabilities in dependencies (package.json, requirements.txt, etc.).")

    fs_path = st.text_input(
        "Path to scan",
        value=".",
        placeholder="e.g., /path/to/project or .",
        help="Directory or file to scan",
        key="fs_path",
    )

    fs_scanners = st.multiselect(
        "Scanners",
        options=["vuln", "secret", "misconfig", "license"],
        default=["vuln", "secret"],
        help="Types of issues to scan for",
        key="fs_scanners",
    )

    if st.button("Scan Filesystem", use_container_width=True, type="primary", key="fs_scan_btn"):
        if fs_path.strip():
            with st.spinner(f"Scanning {fs_path}..."):
                args = {
                    "path": fs_path.strip(),
                    "severity": st.session_state.get("_trivy_severity", "CRITICAL,HIGH,MEDIUM,LOW"),
                    "ignore_unfixed": st.session_state.get("_trivy_ignore_unfixed", False),
                    "skip_db_update": st.session_state.get("_trivy_skip_db_update", False),
                }
                if fs_scanners:
                    args["scanners"] = ",".join(fs_scanners)

                result = _invoke(tools, "trivy_scan_filesystem", args)

                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_trivy_fs_scan"] = result.get("data", {})
                    st.success("Scan completed!")
                else:
                    st.error(f"Scan failed: {result}")
        else:
            st.warning("Please enter a path")

    # Display filesystem scan results
    fs_scan_data = st.session_state.get("_trivy_fs_scan", {})
    if fs_scan_data:
        counts = _count_vulnerabilities(fs_scan_data)
        total = sum(counts.values())

        if total > 0:
            st.markdown(f"**Total vulnerabilities found:** {total}")

            vulns = _extract_vulnerabilities(fs_scan_data)
            for vuln in vulns[:30]:
                severity_class = f"severity-{vuln['severity'].lower()}"
                st.markdown(
                    f"<span class='{severity_class}'>{vuln['severity']}</span> "
                    f"**{vuln['id']}** in `{vuln['pkg']}` ({vuln['target']})",
                    unsafe_allow_html=True,
                )
        else:
            st.success("No vulnerabilities found!")

        with st.expander("Raw JSON Results", expanded=False):
            st.json(fs_scan_data)

    st.markdown('</div>', unsafe_allow_html=True)

# --- REPOSITORY SCAN TAB ---
with tabs[2]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Git Repository Scan")

    st.markdown("Scan a remote git repository for vulnerabilities.")

    repo_url = st.text_input(
        "Repository URL",
        placeholder="https://github.com/user/repo",
        help="Git repository URL to scan",
    )

    repo_branch = st.text_input(
        "Branch (optional)",
        placeholder="main",
        help="Specific branch to scan",
    )

    if st.button("Scan Repository", use_container_width=True, type="primary", key="repo_scan_btn"):
        if repo_url.strip():
            with st.spinner(f"Scanning {repo_url}... (this may take several minutes)"):
                args = {
                    "repo_url": repo_url.strip(),
                    "severity": st.session_state.get("_trivy_severity", "CRITICAL,HIGH,MEDIUM,LOW"),
                    "ignore_unfixed": st.session_state.get("_trivy_ignore_unfixed", False),
                    "skip_db_update": st.session_state.get("_trivy_skip_db_update", False),
                }
                if repo_branch.strip():
                    args["branch"] = repo_branch.strip()

                result = _invoke(tools, "trivy_scan_repo", args)

                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_trivy_repo_scan"] = result.get("data", {})
                    st.success("Scan completed!")
                else:
                    st.error(f"Scan failed: {result}")
        else:
            st.warning("Please enter a repository URL")

    # Display repo scan results
    repo_scan_data = st.session_state.get("_trivy_repo_scan", {})
    if repo_scan_data:
        counts = _count_vulnerabilities(repo_scan_data)
        total = sum(counts.values())
        st.markdown(f"**Total vulnerabilities found:** {total}")

        with st.expander("Raw JSON Results", expanded=False):
            st.json(repo_scan_data)

    st.markdown('</div>', unsafe_allow_html=True)

# --- IAC SCAN TAB ---
with tabs[3]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Infrastructure as Code (IaC) Scan")

    st.markdown("Scan Terraform, Kubernetes manifests, Dockerfiles, and other IaC files for misconfigurations.")

    iac_path = st.text_input(
        "Path to scan",
        placeholder="e.g., /path/to/terraform or ./k8s-manifests",
        help="Directory containing IaC files",
        key="iac_path",
    )

    if st.button("Scan IaC", use_container_width=True, type="primary", key="iac_scan_btn"):
        if iac_path.strip():
            with st.spinner(f"Scanning {iac_path}..."):
                args = {
                    "path": iac_path.strip(),
                    "severity": st.session_state.get("_trivy_severity", "CRITICAL,HIGH,MEDIUM,LOW"),
                    "skip_db_update": st.session_state.get("_trivy_skip_db_update", False),
                }

                result = _invoke(tools, "trivy_scan_config", args)

                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_trivy_iac_scan"] = result.get("data", {})
                    st.success("Scan completed!")
                else:
                    st.error(f"Scan failed: {result}")
        else:
            st.warning("Please enter a path")

    # Display IaC scan results
    iac_scan_data = st.session_state.get("_trivy_iac_scan", {})
    if iac_scan_data:
        results = iac_scan_data.get("Results", [])
        misconfigs_count = 0
        for result in results:
            misconfigs_count += len(result.get("Misconfigurations", []))

        st.markdown(f"**Misconfigurations found:** {misconfigs_count}")

        for result in results:
            target = result.get("Target", "Unknown")
            misconfigs = result.get("Misconfigurations", [])
            if misconfigs:
                st.markdown(f"**{target}**")
                for mc in misconfigs:
                    severity = mc.get("Severity", "UNKNOWN")
                    severity_class = f"severity-{severity.lower()}"
                    st.markdown(
                        f"<span class='{severity_class}'>{severity}</span> "
                        f"**{mc.get('ID', '')}**: {mc.get('Title', '')}",
                        unsafe_allow_html=True,
                    )

        with st.expander("Raw JSON Results", expanded=False):
            st.json(iac_scan_data)

    st.markdown('</div>', unsafe_allow_html=True)

# --- SBOM TAB ---
with tabs[4]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Software Bill of Materials (SBOM)")

    col_gen, col_scan = st.columns(2)

    with col_gen:
        st.markdown("#### Generate SBOM")

        sbom_target = st.text_input(
            "Target",
            placeholder="e.g., nginx:latest or /path/to/project",
            help="Image or path to generate SBOM for",
            key="sbom_gen_target",
        )

        sbom_type = st.selectbox(
            "Target type",
            options=["image", "filesystem"],
            index=0,
            help="Type of target",
        )

        sbom_format = st.selectbox(
            "Output format",
            options=["cyclonedx", "spdx", "spdx-json"],
            index=0,
            help="SBOM format",
        )

        if st.button("Generate SBOM", use_container_width=True, type="primary", key="sbom_gen_btn"):
            if sbom_target.strip():
                with st.spinner("Generating SBOM..."):
                    result = _invoke(tools, "trivy_generate_sbom", {
                        "target": sbom_target.strip(),
                        "target_type": sbom_type,
                        "output_format": sbom_format,
                    })

                    if isinstance(result, dict) and result.get("ok"):
                        st.session_state["_trivy_sbom"] = result.get("sbom", "")
                        st.success("SBOM generated!")
                    else:
                        st.error(f"Failed: {result}")
            else:
                st.warning("Please enter a target")

        sbom_content = st.session_state.get("_trivy_sbom", "")
        if sbom_content:
            st.download_button(
                "Download SBOM",
                data=sbom_content,
                file_name=f"sbom.{sbom_format}.json" if "json" in sbom_format else f"sbom.{sbom_format}.xml",
                mime="application/json" if "json" in sbom_format else "application/xml",
            )
            with st.expander("Preview SBOM", expanded=False):
                st.code(sbom_content[:5000] + "..." if len(sbom_content) > 5000 else sbom_content)

    with col_scan:
        st.markdown("#### Scan SBOM")

        sbom_path = st.text_input(
            "SBOM file path",
            placeholder="/path/to/sbom.json",
            help="Path to SBOM file (CycloneDX or SPDX format)",
            key="sbom_scan_path",
        )

        if st.button("Scan SBOM", use_container_width=True, type="primary", key="sbom_scan_btn"):
            if sbom_path.strip():
                with st.spinner("Scanning SBOM..."):
                    result = _invoke(tools, "trivy_scan_sbom", {
                        "sbom_path": sbom_path.strip(),
                        "severity": st.session_state.get("_trivy_severity", "CRITICAL,HIGH,MEDIUM,LOW"),
                        "ignore_unfixed": st.session_state.get("_trivy_ignore_unfixed", False),
                        "skip_db_update": st.session_state.get("_trivy_skip_db_update", False),
                    })

                    if isinstance(result, dict) and result.get("ok"):
                        st.session_state["_trivy_sbom_scan"] = result.get("data", {})
                        st.success("Scan completed!")
                    else:
                        st.error(f"Scan failed: {result}")
            else:
                st.warning("Please enter an SBOM file path")

        sbom_scan_data = st.session_state.get("_trivy_sbom_scan", {})
        if sbom_scan_data:
            counts = _count_vulnerabilities(sbom_scan_data)
            total = sum(counts.values())
            st.markdown(f"**Vulnerabilities found:** {total}")

    st.markdown('</div>', unsafe_allow_html=True)

# --- TOOLS & DEBUG TAB ---
with tabs[5]:
    st.markdown('<div class="trivy-card">', unsafe_allow_html=True)
    st.markdown("### Available MCP Tools")

    col_info, col_refresh = st.columns([3, 1])

    with col_info:
        st.markdown(f"**Loaded Tools:** {len(tools)}")

    with col_refresh:
        if st.button("Reload Tools", use_container_width=True, key="reload_tools"):
            tools = _get_trivy_tools(force_reload=True)
            st.success("Tools reloaded!")
            st.rerun()

    # List all available tools
    with st.expander("Show All Tool Names", expanded=False):
        if tools:
            for idx, tool in enumerate(tools, 1):
                tool_name = tool.get("name", "unknown") if isinstance(tool, dict) else str(tool)
                st.markdown(f"{idx}. `{tool_name}`")
        else:
            st.info("No tools available")

    st.divider()

    # Health check
    st.markdown("### Trivy Health Check")

    if st.button("Run Health Check", use_container_width=True, key="health_check"):
        with st.spinner("Checking Trivy availability..."):
            health_result = _invoke(tools, "trivy_health_check", {})
            if isinstance(health_result, dict):
                if health_result.get("ok"):
                    st.success("Trivy is available")
                else:
                    st.error("Trivy reported issues")
                st.json(health_result)
            else:
                st.error("Unexpected health check response format")
                st.code(str(health_result))

    st.divider()

    # Database management
    st.markdown("### Database Management")

    col_db1, col_db2 = st.columns(2)

    with col_db1:
        if st.button("Update Vulnerability DB", use_container_width=True, key="update_db"):
            with st.spinner("Updating database... (this may take a few minutes)"):
                result = _invoke(tools, "trivy_update_db", {})
                if isinstance(result, dict) and result.get("ok"):
                    st.success("Database updated!")
                else:
                    st.error(f"Failed: {result}")

    with col_db2:
        if st.button("Clean Cache", use_container_width=True, key="clean_cache"):
            with st.spinner("Cleaning cache..."):
                result = _invoke(tools, "trivy_clean_cache", {})
                if isinstance(result, dict) and result.get("ok"):
                    st.success("Cache cleaned!")
                else:
                    st.error(f"Failed: {result}")

    st.divider()

    # List plugins
    st.markdown("### Installed Plugins")

    if st.button("List Plugins", use_container_width=True, key="list_plugins"):
        with st.spinner("Listing plugins..."):
            result = _invoke(tools, "trivy_list_plugins", {})
            if isinstance(result, dict) and result.get("ok"):
                plugins = result.get("plugins", [])
                if plugins:
                    for plugin in plugins:
                        st.markdown(f"- {plugin}")
                else:
                    st.info("No plugins installed")
            else:
                st.error(f"Failed: {result}")

    st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "**Tip:** Trivy scans can take time, especially for large images. "
    "Use 'Skip DB update' for faster subsequent scans if the database is already up to date."
)
