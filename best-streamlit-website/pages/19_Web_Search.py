"""Web Search - Search the web using DuckDuckGo."""

import asyncio
import os
from datetime import datetime

import streamlit as st

from src.admin_config import load_admin_config
from src.theme import set_theme
from src.mcp_health import add_mcp_status_styles


set_theme(page_title="Web Search", page_icon="🔍")

admin = load_admin_config()

add_mcp_status_styles()

# Custom styling
st.markdown(
    """
    <style>
    .search-hero {
        background: linear-gradient(135deg, #f59e0b 0%, #ef4444 50%, #ec4899 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(245, 158, 11, 0.3);
    }
    .search-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .search-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .search-card {
        background: linear-gradient(145deg, #ffffff, #fff7ed);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #fed7aa;
        box-shadow: 0 4px 16px rgba(245, 158, 11, 0.1);
        margin-bottom: 1rem;
    }
    .search-card h3 {
        font-size: 1.1rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #92400e;
    }
    .result-card {
        background: white;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        border: 1px solid #e5e7eb;
        margin-bottom: 0.75rem;
        transition: all 0.2s;
    }
    .result-card:hover {
        border-color: #f59e0b;
        box-shadow: 0 4px 12px rgba(245, 158, 11, 0.15);
    }
    .result-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: #1e40af;
        margin-bottom: 0.25rem;
    }
    .result-title a {
        color: #1e40af;
        text-decoration: none;
    }
    .result-title a:hover {
        text-decoration: underline;
    }
    .result-url {
        font-size: 0.8rem;
        color: #059669;
        margin-bottom: 0.5rem;
    }
    .result-snippet {
        font-size: 0.9rem;
        color: #4b5563;
        line-height: 1.5;
    }
    .news-source {
        font-size: 0.75rem;
        color: #6b7280;
        margin-top: 0.5rem;
    }
    .image-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 1rem;
    }
    .suggestion-chip {
        display: inline-block;
        background: #fef3c7;
        color: #92400e;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        margin: 0.25rem;
        cursor: pointer;
        transition: all 0.2s;
    }
    .suggestion-chip:hover {
        background: #fde68a;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="search-hero">
        <h1>🔍 Web Search</h1>
        <p>Search the web for text, news, images, videos, and places using DuckDuckGo</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Initialize session state
if "search_results" not in st.session_state:
    st.session_state.search_results = None
if "search_query" not in st.session_state:
    st.session_state.search_query = ""
if "search_type" not in st.session_state:
    st.session_state.search_type = "web"
if "search_history" not in st.session_state:
    st.session_state.search_history = []


def _get_mcp_client():
    """Get the Web Search MCP client."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from src.mcp_log import create_logging_interceptor

    url = os.getenv("STREAMLIT_WEBSEARCH_MCP_URL", "http://websearch-mcp:8009")

    interceptor = create_logging_interceptor(source="websearch_page")

    return MultiServerMCPClient(
        {"websearch": {"transport": "sse", "url": url}},
        tool_interceptors=[interceptor],
    )


def _call_tool(tool_name: str, **kwargs):
    """Call a Web Search MCP tool."""
    try:
        client = _get_mcp_client()
        tools = asyncio.run(client.get_tools())

        tool = next((t for t in tools if t.name == tool_name), None)
        if not tool:
            return {"ok": False, "error": f"Tool not found: {tool_name}"}

        result = asyncio.run(tool.ainvoke(kwargs))
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Search bar
st.markdown('<div class="search-card">', unsafe_allow_html=True)

col_search, col_type = st.columns([3, 1])

with col_search:
    search_query = st.text_input(
        "Search the web",
        value=st.session_state.search_query,
        placeholder="Enter your search query...",
        label_visibility="collapsed",
    )

with col_type:
    search_type = st.selectbox(
        "Type",
        options=["web", "news", "images", "videos", "maps"],
        format_func=lambda x: {
            "web": "🌐 Web",
            "news": "📰 News",
            "images": "🖼️ Images",
            "videos": "🎬 Videos",
            "maps": "📍 Places",
        }.get(x, x),
        label_visibility="collapsed",
    )

col_search_btn, col_opts = st.columns([1, 3])

with col_search_btn:
    search_clicked = st.button("Search", use_container_width=True, type="primary")

with col_opts:
    with st.expander("Options"):
        max_results = st.slider("Max Results", 5, 50, 10)
        region = st.selectbox("Region", ["wt-wt (Global)", "us-en", "uk-en", "de-de", "fr-fr"])
        region_code = region.split(" ")[0]

        if search_type in ["news", "videos"]:
            timelimit = st.selectbox("Time Range", ["Any time", "Past day (d)", "Past week (w)", "Past month (m)"])
            timelimit_code = timelimit.split("(")[1].rstrip(")") if "(" in timelimit else None
        else:
            timelimit_code = None

st.markdown('</div>', unsafe_allow_html=True)

# Perform search
if search_clicked and search_query:
    st.session_state.search_query = search_query
    st.session_state.search_type = search_type

    # Add to history
    if search_query not in [h["query"] for h in st.session_state.search_history]:
        st.session_state.search_history.insert(0, {
            "query": search_query,
            "type": search_type,
            "time": datetime.now().strftime("%H:%M"),
        })
        st.session_state.search_history = st.session_state.search_history[:20]

    with st.spinner(f"Searching for '{search_query}'..."):
        if search_type == "web":
            result = _call_tool("websearch_search", query=search_query, max_results=max_results, region=region_code)
        elif search_type == "news":
            kwargs = {"query": search_query, "max_results": max_results}
            if timelimit_code:
                kwargs["timelimit"] = timelimit_code
            result = _call_tool("websearch_news", **kwargs)
        elif search_type == "images":
            result = _call_tool("websearch_images", query=search_query, max_results=max_results)
        elif search_type == "videos":
            kwargs = {"query": search_query, "max_results": max_results}
            if timelimit_code:
                kwargs["timelimit"] = timelimit_code
            result = _call_tool("websearch_videos", **kwargs)
        elif search_type == "maps":
            result = _call_tool("websearch_maps", query=search_query, max_results=max_results)
        else:
            result = {"ok": False, "error": "Unknown search type"}

        if result.get("ok"):
            st.session_state.search_results = result
        else:
            st.error(f"Search failed: {result.get('error')}")
            st.session_state.search_results = None

# Display results
if st.session_state.search_results and st.session_state.search_results.get("ok"):
    results = st.session_state.search_results
    result_list = results.get("results", [])

    st.markdown(f"### Results for: **{results.get('query', '')}**")
    st.caption(f"Found {results.get('count', len(result_list))} results")

    if st.session_state.search_type == "web":
        for r in result_list:
            st.markdown(f'''
            <div class="result-card">
                <div class="result-title"><a href="{r.get('url', '#')}" target="_blank">{r.get('title', 'No title')}</a></div>
                <div class="result-url">{r.get('url', '')[:80]}</div>
                <div class="result-snippet">{r.get('snippet', '')}</div>
            </div>
            ''', unsafe_allow_html=True)

    elif st.session_state.search_type == "news":
        for r in result_list:
            st.markdown(f'''
            <div class="result-card">
                <div class="result-title"><a href="{r.get('url', '#')}" target="_blank">{r.get('title', 'No title')}</a></div>
                <div class="result-url">{r.get('url', '')[:80]}</div>
                <div class="result-snippet">{r.get('snippet', '')}</div>
                <div class="news-source">📰 {r.get('source', 'Unknown')} • {r.get('date', '')}</div>
            </div>
            ''', unsafe_allow_html=True)

    elif st.session_state.search_type == "images":
        cols = st.columns(4)
        for i, r in enumerate(result_list):
            with cols[i % 4]:
                img_url = r.get("thumbnail") or r.get("image_url")
                if img_url:
                    try:
                        st.image(img_url, caption=r.get("title", "")[:30], use_container_width=True)
                    except Exception:
                        st.markdown(f"[{r.get('title', 'Image')[:30]}]({r.get('source_url', '#')})")
                st.caption(f"{r.get('width', '?')}x{r.get('height', '?')}")

    elif st.session_state.search_type == "videos":
        for r in result_list:
            col_thumb, col_info = st.columns([1, 3])
            with col_thumb:
                if r.get("thumbnail"):
                    try:
                        st.image(r.get("thumbnail"), use_container_width=True)
                    except Exception:
                        st.markdown("🎬")
            with col_info:
                st.markdown(f"**[{r.get('title', 'No title')}]({r.get('url', '#')})**")
                st.caption(f"📺 {r.get('publisher', 'Unknown')} • ⏱️ {r.get('duration', 'N/A')}")
                if r.get("views"):
                    st.caption(f"👁️ {r.get('views'):,} views")
                st.markdown(f"<small>{r.get('description', '')[:150]}</small>", unsafe_allow_html=True)
            st.divider()

    elif st.session_state.search_type == "maps":
        for r in result_list:
            with st.container(border=True):
                st.markdown(f"### 📍 {r.get('title', 'Unknown Place')}")
                st.markdown(f"**Address:** {r.get('address', 'N/A')}")
                if r.get("phone"):
                    st.markdown(f"**Phone:** {r.get('phone')}")
                if r.get("rating"):
                    st.markdown(f"**Rating:** {'⭐' * int(r.get('rating', 0))} ({r.get('rating')})")
                if r.get("category"):
                    st.caption(f"Category: {r.get('category')}")
                if r.get("url"):
                    st.markdown(f"[View on Map]({r.get('url')})")

# Sidebar
with st.sidebar:
    st.markdown("### 💡 Instant Answer")

    instant_query = st.text_input("Quick question", placeholder="What is Python?")
    if st.button("Get Answer", use_container_width=True):
        if instant_query:
            with st.spinner("Looking up..."):
                result = _call_tool("websearch_instant_answer", query=instant_query)
                if result.get("ok") and result.get("answer"):
                    st.success("Found an answer!")
                    st.markdown(f"**{result.get('answer')}**")
                    if result.get("url"):
                        st.markdown(f"[Source]({result.get('url')})")
                else:
                    st.info("No instant answer available. Try a web search instead.")

    st.divider()

    st.markdown("### 🔮 Suggestions")

    suggest_query = st.text_input("Get suggestions", placeholder="python programming")
    if st.button("Get Suggestions", use_container_width=True):
        if suggest_query:
            with st.spinner("Fetching suggestions..."):
                result = _call_tool("websearch_suggestions", query=suggest_query)
                if result.get("ok"):
                    suggestions = result.get("suggestions", [])
                    if suggestions:
                        for s in suggestions:
                            if st.button(s, key=f"sug_{hash(s)}", use_container_width=True):
                                st.session_state.search_query = s
                                st.rerun()
                    else:
                        st.caption("No suggestions found.")

    st.divider()

    st.markdown("### 📜 Search History")

    if st.session_state.search_history:
        for h in st.session_state.search_history[:10]:
            icon = {"web": "🌐", "news": "📰", "images": "🖼️", "videos": "🎬", "maps": "📍"}.get(h["type"], "🔍")
            col_h, col_go = st.columns([3, 1])
            with col_h:
                st.caption(f"{icon} {h['query'][:20]}...")
            with col_go:
                if st.button("→", key=f"hist_{hash(h['query'])}"):
                    st.session_state.search_query = h["query"]
                    st.session_state.search_type = h["type"]
                    st.rerun()

        if st.button("Clear History", use_container_width=True):
            st.session_state.search_history = []
            st.rerun()
    else:
        st.caption("Your search history will appear here.")

    st.divider()

    # Health check
    if st.button("🔧 Health Check", use_container_width=True):
        result = _call_tool("websearch_health_check")
        if result.get("ok"):
            st.success("Web Search is available!")
            st.json(result)
        else:
            st.error(f"Health check failed: {result.get('error')}")

# Quick search suggestions at the bottom
if not st.session_state.search_results:
    st.markdown('<div class="search-card">', unsafe_allow_html=True)
    st.markdown("### 💡 Try searching for...")

    suggestions = [
        "Python programming tutorials",
        "Latest tech news",
        "Machine learning explained",
        "Best restaurants near me",
        "Climate change updates",
        "Web development frameworks",
    ]

    cols = st.columns(3)
    for i, sug in enumerate(suggestions):
        with cols[i % 3]:
            if st.button(sug, use_container_width=True, key=f"quick_{i}"):
                st.session_state.search_query = sug
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "**Web Search** uses DuckDuckGo for privacy-focused web searches. "
    "Search for web pages, news, images, videos, and places without tracking. "
    "No API key required."
)
