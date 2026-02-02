"""LangChain Skills Implementation for Agent Lab.

This module provides a skills-based architecture for specialized agent capabilities.
Skills are prompt-driven specializations that agents can invoke contextually.

Key concepts:
- Skills are specialized prompts that augment agent behavior
- Skills can be loaded dynamically based on context
- Skills follow the llms.txt pattern for progressive disclosure
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

from langchain_core.tools import tool


# ─────────────────────────────────────────────────────────────────────────────
# SKILL DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Skill:
    """A specialized capability that can be loaded by an agent."""
    name: str
    description: str
    prompt: str
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    sub_skills: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT DEVELOPER SKILL
# ─────────────────────────────────────────────────────────────────────────────

STREAMLIT_DEVELOPER_SKILL = Skill(
    name="streamlit_developer",
    description="Expert Streamlit/Python developer that generates beautiful, production-ready Streamlit code",
    tags=["python", "streamlit", "ui", "visualization", "dashboard"],
    examples=[
        "Create a dashboard showing system metrics",
        "Build a form for user input with validation",
        "Generate a data visualization with charts",
        "Create an interactive data table with filters",
    ],
    prompt='''You are an expert Streamlit and Python developer specializing in creating beautiful, professional user interfaces.

## Your Expertise
- Creating stunning Streamlit dashboards and applications
- Modern UI/UX design patterns for data applications
- Advanced Streamlit components (charts, tables, forms, layouts)
- Performance optimization for Streamlit apps
- Professional styling with custom CSS

## Code Generation Rules

1. **Always generate complete, runnable code** - Never use placeholders or "..."
2. **Use modern Streamlit features** - st.columns, st.tabs, st.expander, st.metric, st.status
3. **Apply professional styling** - Use custom CSS for gradients, shadows, rounded corners
4. **Include error handling** - Wrap risky operations in try/except
5. **Add helpful comments** - But keep them concise
6. **Use session state properly** - For stateful components
7. **Optimize performance** - Use @st.cache_data and @st.fragment where appropriate

## Visual Design Guidelines

- Use gradient backgrounds for hero sections and cards
- Apply consistent color schemes (blues, purples, greens for status)
- Add subtle shadows and rounded corners
- Use emoji icons sparingly but effectively
- Create responsive layouts with columns
- Include loading states and empty states

## Code Structure Template

```python
import streamlit as st
import pandas as pd
# ... other imports

# Page config (if standalone)
# st.set_page_config(page_title="Title", page_icon="icon", layout="wide")

# Custom CSS
st.markdown("""
<style>
.custom-card {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-radius: 16px;
    padding: 1.5rem;
    color: white;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
</style>
""", unsafe_allow_html=True)

# Main content
def main():
    st.title("Title")

    # Hero section
    st.markdown("""
    <div class="custom-card">
        <h2>Welcome</h2>
        <p>Description</p>
    </div>
    """, unsafe_allow_html=True)

    # Metrics row
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Metric 1", "Value", "Delta")

    # Interactive components
    with st.expander("Details"):
        st.write("Content")

    # Data display
    tab1, tab2 = st.tabs(["Tab 1", "Tab 2"])
    with tab1:
        st.dataframe(data)

if __name__ == "__main__":
    main()
```

## Response Format

When generating code, ALWAYS wrap it in a special marker so it can be rendered:

```streamlit
# Your complete Streamlit code here
```

The code between ```streamlit and ``` markers will be automatically rendered in the Agent Lab interface.

## Important Notes

- Generate COMPLETE code, never partial snippets
- The code will be executed in the Agent Lab context
- Use relative imports if needed (from src.xxx import yyy)
- Access session state via st.session_state
- The code runs inside a Streamlit page, so st.set_page_config is NOT needed
''',
)


DEVOPS_DASHBOARD_SKILL = Skill(
    name="devops_dashboard",
    description="Creates DevOps monitoring dashboards with real-time metrics",
    tags=["devops", "monitoring", "dashboard", "metrics"],
    examples=[
        "Create a Kubernetes cluster health dashboard",
        "Build a CI/CD pipeline status view",
        "Generate a Docker container monitoring panel",
    ],
    prompt='''You are a DevOps dashboard specialist. Generate Streamlit code for infrastructure monitoring.

## Focus Areas
- Real-time metrics display
- Health indicators (red/yellow/green)
- Log viewers and alerts
- Resource utilization charts
- Service status cards

Use the same code generation rules as the streamlit_developer skill.
Wrap your code in ```streamlit markers.
''',
)


DATA_VISUALIZATION_SKILL = Skill(
    name="data_visualization",
    description="Creates advanced data visualizations with Plotly and Altair",
    tags=["visualization", "charts", "plotly", "altair", "data"],
    examples=[
        "Create an interactive time series chart",
        "Build a geographic heatmap",
        "Generate a correlation matrix visualization",
    ],
    prompt='''You are a data visualization expert. Generate Streamlit code with advanced charts.

## Preferred Libraries
- Plotly for interactive charts
- Altair for declarative visualizations
- Built-in st.line_chart, st.bar_chart for simple cases

## Best Practices
- Always include chart titles and axis labels
- Use appropriate color scales
- Add interactivity (tooltips, zoom, selection)
- Handle missing data gracefully

Wrap your code in ```streamlit markers.
''',
)


# ─────────────────────────────────────────────────────────────────────────────
# SKILL REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

SKILL_REGISTRY: Dict[str, Skill] = {
    "streamlit_developer": STREAMLIT_DEVELOPER_SKILL,
    "devops_dashboard": DEVOPS_DASHBOARD_SKILL,
    "data_visualization": DATA_VISUALIZATION_SKILL,
}


def get_skill(skill_name: str) -> Optional[Skill]:
    """Get a skill by name."""
    return SKILL_REGISTRY.get(skill_name)


def list_skills() -> List[Dict[str, Any]]:
    """List all available skills."""
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "tags": skill.tags,
            "examples": skill.examples,
        }
        for skill in SKILL_REGISTRY.values()
    ]


def get_skill_prompt(skill_name: str) -> str:
    """Get the prompt for a skill."""
    skill = get_skill(skill_name)
    return skill.prompt if skill else ""


# ─────────────────────────────────────────────────────────────────────────────
# SKILL TOOLS FOR LANGCHAIN
# ─────────────────────────────────────────────────────────────────────────────

@tool("load_skill")
def load_skill(skill_name: str) -> str:
    """Load a specialized skill prompt to enhance your capabilities.

    Available skills:
    - streamlit_developer: Generate beautiful Streamlit code
    - devops_dashboard: Create DevOps monitoring dashboards
    - data_visualization: Create advanced data visualizations

    Args:
        skill_name: Name of the skill to load

    Returns:
        The skill prompt with specialized instructions
    """
    skill = get_skill(skill_name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys())
        return f"Unknown skill: {skill_name}. Available skills: {available}"

    return f"[SKILL LOADED: {skill.name}]\n\n{skill.prompt}"


@tool("list_available_skills")
def list_available_skills() -> str:
    """List all available skills that can be loaded.

    Returns:
        A formatted list of available skills with descriptions
    """
    lines = ["Available Skills:\n"]
    for skill in SKILL_REGISTRY.values():
        lines.append(f"- **{skill.name}**: {skill.description}")
        if skill.examples:
            lines.append(f"  Examples: {', '.join(skill.examples[:2])}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CODE EXTRACTION UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def extract_streamlit_code(response: str) -> Optional[str]:
    """Extract Streamlit code from an agent response.

    Looks for code blocks marked with ```streamlit or ```python
    and returns the code content.
    """
    # Try streamlit-specific marker first
    streamlit_pattern = r'```streamlit\n(.*?)```'
    matches = re.findall(streamlit_pattern, response, re.DOTALL)
    if matches:
        return matches[0].strip()

    # Fall back to python marker
    python_pattern = r'```python\n(.*?)```'
    matches = re.findall(python_pattern, response, re.DOTALL)
    if matches:
        # Return the longest match (likely the main code block)
        return max(matches, key=len).strip()

    return None


def validate_streamlit_code(code: str) -> Dict[str, Any]:
    """Validate that the code is safe to execute.

    Returns:
        Dict with 'valid' bool and 'errors' list
    """
    errors = []

    # Check for dangerous patterns
    dangerous_patterns = [
        (r'\bos\.system\s*\(', "os.system() is not allowed"),
        (r'\bsubprocess\.\w+\s*\(', "subprocess calls are not allowed"),
        (r'\bexec\s*\(', "exec() is not allowed"),
        (r'\beval\s*\(', "eval() is not allowed"),
        (r'\b__import__\s*\(', "__import__() is not allowed"),
        (r'\bopen\s*\([^)]*["\']w["\']', "Writing files is not allowed"),
        (r'\bshutil\.rmtree', "shutil.rmtree is not allowed"),
    ]

    for pattern, message in dangerous_patterns:
        if re.search(pattern, code):
            errors.append(message)

    # Check for required imports
    if 'import streamlit' not in code and 'from streamlit' not in code:
        if 'st.' in code:
            # Add implicit import
            pass  # Will be handled by execution wrapper

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def wrap_streamlit_code(code: str) -> str:
    """Wrap code to ensure it can run in the Agent Lab context.

    Adds necessary imports and wraps in a function if needed.
    """
    lines = []

    # Ensure streamlit import
    if 'import streamlit as st' not in code:
        lines.append('import streamlit as st')

    # Add common imports if used but not imported
    if 'pd.' in code and 'import pandas' not in code:
        lines.append('import pandas as pd')
    if 'np.' in code and 'import numpy' not in code:
        lines.append('import numpy as np')
    if 'px.' in code and 'import plotly' not in code:
        lines.append('import plotly.express as px')
    if 'go.' in code and 'import plotly.graph' not in code:
        lines.append('import plotly.graph_objects as go')

    if lines:
        return '\n'.join(lines) + '\n\n' + code
    return code


# ─────────────────────────────────────────────────────────────────────────────
# NAMESPACE-AWARE TOOL WRAPPER
# ─────────────────────────────────────────────────────────────────────────────

def namespace_tool(tool_obj: Any, namespace: str) -> Any:
    """Wrap a tool with a namespace prefix to avoid name collisions.

    Args:
        tool_obj: The original LangChain tool
        namespace: The namespace prefix (e.g., "kubernetes", "docker")

    Returns:
        A wrapped tool with namespaced name
    """
    original_name = getattr(tool_obj, 'name', str(tool_obj))

    # Skip if already namespaced
    if original_name.startswith(f"{namespace}__"):
        return tool_obj

    # Create new name with namespace
    new_name = f"{namespace}__{original_name}"

    # Update tool name
    if hasattr(tool_obj, 'name'):
        tool_obj.name = new_name

    # Update description to include namespace
    if hasattr(tool_obj, 'description'):
        original_desc = tool_obj.description or ""
        tool_obj.description = f"[{namespace}] {original_desc}"

    return tool_obj


def namespace_tools(tools: List[Any], namespace: str) -> List[Any]:
    """Apply namespace prefix to a list of tools.

    Args:
        tools: List of LangChain tools
        namespace: The namespace prefix

    Returns:
        List of tools with namespaced names
    """
    return [namespace_tool(t, namespace) for t in tools]


def get_tool_namespace(tool_name: str) -> Optional[str]:
    """Extract namespace from a namespaced tool name.

    Args:
        tool_name: The tool name (e.g., "kubernetes__list_pods")

    Returns:
        The namespace or None if not namespaced
    """
    if "__" in tool_name:
        return tool_name.split("__")[0]
    return None


def get_tool_base_name(tool_name: str) -> str:
    """Extract base name from a namespaced tool name.

    Args:
        tool_name: The tool name (e.g., "kubernetes__list_pods")

    Returns:
        The base name without namespace
    """
    if "__" in tool_name:
        return "__".join(tool_name.split("__")[1:])
    return tool_name
