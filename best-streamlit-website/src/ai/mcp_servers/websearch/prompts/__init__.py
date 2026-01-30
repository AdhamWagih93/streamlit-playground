from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="research_plan",
        title="Research plan",
        description="Create a focused web research plan using Web Search MCP tools.",
        tags={"websearch", "research"},
    )
    def research_plan(question: str, constraints: str = "") -> str:
        return """You are a research assistant.

Question:
{question}

Constraints:
{constraints}

Approach:
1) Generate 3-5 targeted queries.
2) Prefer authoritative sources (official docs, vendor pages).
3) Cross-check claims across at least 2 sources.
4) Summarize with links + key takeaways.

Return:
- queries: [..]
- then the tool calls to run
- then a structured summary template
""".format(question=question, constraints=constraints)

    @mcp.prompt(
        name="source_credibility",
        title="Source credibility checklist",
        description="Checklist for evaluating web sources.",
        tags={"websearch"},
    )
    def source_credibility(topic: str = "") -> str:
        return """Create a credibility checklist for sources.

Topic (optional):
{topic}

Include criteria like: recency, authoritativeness, primary vs secondary, and bias.
""".format(topic=topic)
