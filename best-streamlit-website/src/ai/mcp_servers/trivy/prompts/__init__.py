from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="guided_workflow",
        title="Guided workflow",
        description="Plan vulnerability scans and remediation using Trivy MCP tools.",
        tags={"trivy", "security", "workflow"},
    )
    def guided_workflow(target: str, policy: str = "") -> str:
        return """You are a security scanner assistant.

Target:
{target}

Policy/constraints (optional):
{policy}

Approach:
1) Run the smallest relevant scan (image/fs/repo) for the target.
2) Summarize critical/high issues first.
3) Propose fixes that preserve functionality (upgrade base image, pin deps).
4) Re-scan to confirm improvement.

Output:
- Tool calls
- Findings summary
- Remediation plan
""".format(target=target, policy=policy)

    @mcp.prompt(
        name="remediation_report",
        title="Remediation report",
        description="Structure a remediation report for a scan output.",
        tags={"trivy"},
    )
    def remediation_report(context: str) -> str:
        return """Turn the scan context into a remediation report:
- Top findings (severity grouped)
- Affected components
- Suggested fixes
- Verification steps

Scan context:
{context}
""".format(context=context)
