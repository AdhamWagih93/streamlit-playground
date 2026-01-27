"""Playwright Browser - Interactive browser automation and web scraping."""

from datetime import datetime
import os

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.theme import set_theme


set_theme(page_title="Playwright Browser", page_icon="🎭")

admin = load_admin_config()

add_mcp_status_styles()

# Custom styling
st.markdown(
    """
    <style>
    .pw-hero {
        background: linear-gradient(135deg, #059669 0%, #0891b2 50%, #6366f1 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(5, 150, 105, 0.3);
    }
    .pw-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .pw-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .pw-card {
        background: linear-gradient(145deg, #ffffff, #f0fdf4);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #d1fae5;
        box-shadow: 0 4px 16px rgba(5, 150, 105, 0.1);
        margin-bottom: 1rem;
    }
    .pw-card h3 {
        font-size: 1.1rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #065f46;
    }
    .url-display {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        font-family: monospace;
        font-size: 0.9rem;
        word-break: break-all;
    }
    .action-btn {
        transition: all 0.2s;
    }
    .screenshot-preview {
        border: 2px solid #d1fae5;
        border-radius: 12px;
        overflow: hidden;
        margin-top: 1rem;
    }
    .link-item {
        padding: 0.5rem;
        border-bottom: 1px solid #e5e7eb;
        font-size: 0.85rem;
    }
    .link-item:hover {
        background: #f0fdf4;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="pw-hero">
        <h1>🎭 Playwright Browser</h1>
        <p>Automate web browsing, take screenshots, and extract content from any website</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Initialize session state
if "pw_url" not in st.session_state:
    st.session_state.pw_url = ""
if "pw_content" not in st.session_state:
    st.session_state.pw_content = None
if "pw_screenshot" not in st.session_state:
    st.session_state.pw_screenshot = None
if "pw_links" not in st.session_state:
    st.session_state.pw_links = []
if "pw_history" not in st.session_state:
    st.session_state.pw_history = []


def _get_playwright_client():
    """Get the Playwright MCP client."""
    return get_mcp_client("playwright")


def _call_tool(tool_name: str, **kwargs):
    """Call a Playwright MCP tool."""
    try:
        client = _get_playwright_client()
        return client.invoke(tool_name, kwargs)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Main layout
col_nav, col_actions = st.columns([2, 1])

with col_nav:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### 🌐 Navigation")

    url_input = st.text_input(
        "Enter URL",
        value=st.session_state.pw_url,
        placeholder="https://example.com",
        label_visibility="collapsed",
    )

    col_go, col_back, col_fwd, col_refresh = st.columns(4)

    with col_go:
        if st.button("Navigate", use_container_width=True, type="primary"):
            if url_input:
                with st.spinner("Loading page..."):
                    result = _call_tool("playwright_navigate", url=url_input)
                    if result.get("ok"):
                        st.session_state.pw_url = result.get("url", url_input)
                        st.session_state.pw_history.append({
                            "url": st.session_state.pw_url,
                            "title": result.get("title", ""),
                            "time": datetime.now().strftime("%H:%M:%S"),
                        })
                        st.success(f"Loaded: {result.get('title', 'Page')}")
                    else:
                        st.error(f"Failed: {result.get('error', 'Unknown error')}")

    with col_back:
        if st.button("← Back", use_container_width=True):
            result = _call_tool("playwright_back")
            if result.get("ok"):
                st.session_state.pw_url = result.get("url", "")
                st.rerun()

    with col_fwd:
        if st.button("Forward →", use_container_width=True):
            result = _call_tool("playwright_forward")
            if result.get("ok"):
                st.session_state.pw_url = result.get("url", "")
                st.rerun()

    with col_refresh:
        if st.button("↻ Refresh", use_container_width=True):
            if st.session_state.pw_url:
                result = _call_tool("playwright_navigate", url=st.session_state.pw_url)
                st.rerun()

    # Current page info
    if st.session_state.pw_url:
        st.markdown(f'<div class="url-display">{st.session_state.pw_url}</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

with col_actions:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### ⚡ Quick Actions")

    if st.button("📸 Screenshot", use_container_width=True):
        with st.spinner("Taking screenshot..."):
            result = _call_tool("playwright_screenshot", full_page=False)
            if result.get("ok"):
                st.session_state.pw_screenshot = result
                st.success("Screenshot captured!")
            else:
                st.error(f"Failed: {result.get('error')}")

    if st.button("📄 Full Page Screenshot", use_container_width=True):
        with st.spinner("Taking full page screenshot..."):
            result = _call_tool("playwright_screenshot", full_page=True)
            if result.get("ok"):
                st.session_state.pw_screenshot = result
                st.success("Full page screenshot captured!")
            else:
                st.error(f"Failed: {result.get('error')}")

    if st.button("🔗 Extract Links", use_container_width=True):
        with st.spinner("Extracting links..."):
            result = _call_tool("playwright_get_links")
            if result.get("ok"):
                st.session_state.pw_links = result.get("links", [])
                st.success(f"Found {result.get('count', 0)} links!")
            else:
                st.error(f"Failed: {result.get('error')}")

    if st.button("📝 Get Content", use_container_width=True):
        with st.spinner("Extracting content..."):
            result = _call_tool("playwright_get_content")
            if result.get("ok"):
                st.session_state.pw_content = result
                st.success("Content extracted!")
            else:
                st.error(f"Failed: {result.get('error')}")

    st.markdown('</div>', unsafe_allow_html=True)

# Tabs for different views
tab_screenshot, tab_content, tab_links, tab_interact, tab_history = st.tabs([
    "📸 Screenshot", "📝 Content", "🔗 Links", "🎯 Interact", "📜 History"
])

with tab_screenshot:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### Screenshot Preview")

    if st.session_state.pw_screenshot:
        screenshot = st.session_state.pw_screenshot

        # Try to read the actual file
        filepath = screenshot.get("filepath")
        if filepath and os.path.exists(filepath):
            st.image(filepath, caption=f"Screenshot of {screenshot.get('url', 'page')}")

            with open(filepath, "rb") as f:
                st.download_button(
                    "Download Screenshot",
                    f.read(),
                    file_name=screenshot.get("filename", "screenshot.png"),
                    mime="image/png",
                    use_container_width=True,
                )
        else:
            st.info("Screenshot saved but file not accessible from this location.")
            st.json(screenshot)
    else:
        st.info("Click **Screenshot** to capture the current page.")

    st.markdown('</div>', unsafe_allow_html=True)

with tab_content:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### Page Content")

    if st.session_state.pw_content:
        content = st.session_state.pw_content

        st.markdown(f"**URL:** {content.get('url', 'N/A')}")
        st.markdown(f"**Title:** {content.get('title', 'N/A')}")

        st.divider()

        st.markdown("**Text Content:**")
        st.text_area(
            "Content",
            value=content.get("content", "")[:10000],
            height=400,
            label_visibility="collapsed",
        )
    else:
        st.info("Click **Get Content** to extract page text.")

    # Selector-based extraction
    st.divider()
    st.markdown("**Extract by Selector:**")

    selector_input = st.text_input("CSS Selector", placeholder="e.g., h1, .title, #main-content")
    if st.button("Extract Element"):
        if selector_input:
            with st.spinner("Extracting..."):
                result = _call_tool("playwright_get_content", selector=selector_input)
                if result.get("ok"):
                    st.success("Element extracted!")
                    st.text_area("Element Content", result.get("content", ""), height=200)
                else:
                    st.error(f"Failed: {result.get('error')}")

    st.markdown('</div>', unsafe_allow_html=True)

with tab_links:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### Extracted Links")

    if st.session_state.pw_links:
        st.markdown(f"**Found {len(st.session_state.pw_links)} links**")

        # Search filter
        link_filter = st.text_input("Filter links", placeholder="Search...")

        filtered_links = st.session_state.pw_links
        if link_filter:
            filtered_links = [
                l for l in filtered_links
                if link_filter.lower() in l.get("text", "").lower()
                or link_filter.lower() in l.get("href", "").lower()
            ]

        for link in filtered_links[:30]:  # Limit display
            col_text, col_nav = st.columns([4, 1])
            with col_text:
                st.markdown(f'<div class="link-item"><strong>{link.get("text", "No text")[:50]}</strong><br/><small>{link.get("href", "")[:80]}</small></div>', unsafe_allow_html=True)
            with col_nav:
                if st.button("Go", key=f"link_{hash(link.get('href', ''))}"):
                    with st.spinner("Navigating..."):
                        result = _call_tool("playwright_navigate", url=link.get("href"))
                        if result.get("ok"):
                            st.session_state.pw_url = result.get("url")
                            st.rerun()
    else:
        st.info("Click **Extract Links** to find all links on the page.")

    st.markdown('</div>', unsafe_allow_html=True)

with tab_interact:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### Page Interaction")

    col_click, col_fill = st.columns(2)

    with col_click:
        st.markdown("**Click Element**")
        click_selector = st.text_input("Selector to click", placeholder="button, .submit-btn")
        if st.button("Click", use_container_width=True):
            if click_selector:
                with st.spinner("Clicking..."):
                    result = _call_tool("playwright_click", selector=click_selector)
                    if result.get("ok"):
                        st.success(f"Clicked: {click_selector}")
                        st.session_state.pw_url = result.get("url", st.session_state.pw_url)
                    else:
                        st.error(f"Failed: {result.get('error')}")

    with col_fill:
        st.markdown("**Fill Input**")
        fill_selector = st.text_input("Input selector", placeholder="input[name='search']")
        fill_value = st.text_input("Value to fill", placeholder="Enter text...")
        if st.button("Fill", use_container_width=True):
            if fill_selector and fill_value:
                with st.spinner("Filling..."):
                    result = _call_tool("playwright_fill", selector=fill_selector, value=fill_value)
                    if result.get("ok"):
                        st.success("Field filled!")
                    else:
                        st.error(f"Failed: {result.get('error')}")

    st.divider()

    col_key, col_scroll = st.columns(2)

    with col_key:
        st.markdown("**Press Key**")
        key_input = st.selectbox("Key", ["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space"])
        key_selector = st.text_input("Focus selector (optional)", placeholder="input#search")
        if st.button("Press", use_container_width=True):
            with st.spinner("Pressing key..."):
                kwargs = {"key": key_input}
                if key_selector:
                    kwargs["selector"] = key_selector
                result = _call_tool("playwright_press_key", **kwargs)
                if result.get("ok"):
                    st.success(f"Pressed: {key_input}")
                else:
                    st.error(f"Failed: {result.get('error')}")

    with col_scroll:
        st.markdown("**Scroll Page**")
        scroll_dir = st.selectbox("Direction", ["down", "up"])
        scroll_amount = st.slider("Amount (px)", 100, 2000, 500, 100)
        if st.button("Scroll", use_container_width=True):
            with st.spinner("Scrolling..."):
                result = _call_tool("playwright_scroll", direction=scroll_dir, amount=scroll_amount)
                if result.get("ok"):
                    st.success(f"Scrolled {scroll_dir} {scroll_amount}px")
                else:
                    st.error(f"Failed: {result.get('error')}")

    st.divider()

    st.markdown("**Execute JavaScript**")
    js_code = st.text_area("JavaScript", placeholder="return document.title;", height=100)
    if st.button("Execute", use_container_width=True):
        if js_code:
            with st.spinner("Executing..."):
                result = _call_tool("playwright_evaluate", script=js_code)
                if result.get("ok"):
                    st.success("Script executed!")
                    st.code(result.get("result", "No result"), language="json")
                else:
                    st.error(f"Failed: {result.get('error')}")

    st.markdown('</div>', unsafe_allow_html=True)

with tab_history:
    st.markdown('<div class="pw-card">', unsafe_allow_html=True)
    st.markdown("### Browsing History")

    if st.session_state.pw_history:
        for i, entry in enumerate(reversed(st.session_state.pw_history[-20:])):
            col_info, col_go = st.columns([4, 1])
            with col_info:
                st.markdown(f"**{entry.get('title', 'Untitled')}**")
                st.caption(f"{entry.get('url', '')} • {entry.get('time', '')}")
            with col_go:
                if st.button("Go", key=f"hist_{i}"):
                    with st.spinner("Navigating..."):
                        result = _call_tool("playwright_navigate", url=entry.get("url"))
                        if result.get("ok"):
                            st.session_state.pw_url = result.get("url")
                            st.rerun()
            st.divider()

        if st.button("Clear History", use_container_width=True):
            st.session_state.pw_history = []
            st.rerun()
    else:
        st.info("Your browsing history will appear here.")

    st.markdown('</div>', unsafe_allow_html=True)

# Sidebar with forms
with st.sidebar:
    st.markdown("### 📋 Quick Forms")

    result = _call_tool("playwright_get_forms")
    if result.get("ok") and result.get("forms"):
        forms = result.get("forms", [])
        st.markdown(f"**{len(forms)} form(s) detected**")

        for i, form in enumerate(forms[:5]):
            with st.expander(f"Form {i + 1}: {form.get('action', 'No action')[:30]}"):
                st.markdown(f"**Method:** {form.get('method', 'GET').upper()}")
                st.markdown(f"**Action:** {form.get('action', 'N/A')}")
                st.markdown("**Inputs:**")
                for inp in form.get("inputs", [])[:10]:
                    st.markdown(f"- `{inp.get('name', inp.get('id', 'unnamed'))}` ({inp.get('type', 'text')})")
    else:
        st.caption("Navigate to a page to see forms.")

    st.divider()

    st.markdown("### 🔧 Browser Control")

    if st.button("Close Browser", use_container_width=True, type="secondary"):
        result = _call_tool("playwright_close")
        if result.get("ok"):
            st.success("Browser closed!")
            st.session_state.pw_url = ""
            st.session_state.pw_content = None
            st.session_state.pw_screenshot = None
            st.session_state.pw_links = []
        else:
            st.error(f"Failed: {result.get('error')}")

    # Health check
    st.divider()
    if st.button("Health Check", use_container_width=True):
        result = _call_tool("playwright_health_check")
        if result.get("ok"):
            st.success("Playwright is available!")
            st.json(result)
        else:
            st.error(f"Health check failed: {result.get('error')}")

# Footer
st.divider()
st.caption(
    "**Playwright Browser** provides automated web browsing capabilities. "
    "Navigate to websites, take screenshots, extract content, and interact with page elements. "
    "Perfect for web scraping, testing, and automation tasks."
)
