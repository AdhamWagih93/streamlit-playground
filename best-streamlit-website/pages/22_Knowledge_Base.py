"""Knowledge Base - Document upload, vectorization, and search."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

from src.theme import set_theme


set_theme(page_title="Knowledge Base", page_icon="📚")


# =============================================================================
# STYLES
# =============================================================================

st.markdown(
    """
    <style>
    .kb-hero {
        background: linear-gradient(135deg, #7c3aed 0%, #8b5cf6 50%, #a78bfa 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(124, 58, 237, 0.3);
    }
    .kb-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
    }
    .kb-hero p {
        opacity: 0.9;
        margin: 0;
    }
    .stat-card {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        text-align: center;
        transition: all 0.3s ease;
    }
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    .stat-value {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(135deg, #7c3aed, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stat-label {
        color: #64748b;
        font-size: 0.9rem;
        margin-top: 0.5rem;
        font-weight: 500;
    }
    .doc-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        border: 2px solid #e2e8f0;
        margin-bottom: 1rem;
        transition: all 0.2s;
    }
    .doc-card:hover {
        border-color: #7c3aed;
        box-shadow: 0 4px 16px rgba(124, 58, 237, 0.15);
    }
    .doc-title {
        font-weight: 600;
        color: #1e293b;
        font-size: 1.1rem;
    }
    .doc-meta {
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 0.25rem;
    }
    .search-result {
        background: linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);
        border: 1px solid #c4b5fd;
        border-radius: 12px;
        padding: 1rem;
        margin: 0.75rem 0;
    }
    .search-result-header {
        font-weight: 600;
        color: #5b21b6;
        font-size: 0.9rem;
        margin-bottom: 0.5rem;
    }
    .search-result-text {
        color: #374151;
        font-size: 0.95rem;
        line-height: 1.6;
    }
    .relevance-badge {
        display: inline-block;
        background: #7c3aed;
        color: white;
        padding: 0.2rem 0.6rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .upload-zone {
        border: 2px dashed #c4b5fd;
        border-radius: 16px;
        padding: 2rem;
        text-align: center;
        background: #faf5ff;
        transition: all 0.2s;
    }
    .upload-zone:hover {
        border-color: #7c3aed;
        background: #f5f3ff;
    }
    .connection-status {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 500;
    }
    .connection-status.connected {
        background: #dcfce7;
        color: #166534;
    }
    .connection-status.disconnected {
        background: #fee2e2;
        color: #991b1b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# KNOWLEDGE BASE CLIENT
# =============================================================================


def get_kb_client_safe():
    """Get Knowledge Base client with error handling."""
    try:
        from src.knowledge_base import get_kb_client
        return get_kb_client()
    except ImportError as e:
        st.error(f"Knowledge Base module not available: {e}")
        return None
    except Exception as e:
        st.error(f"Could not connect to ChromaDB: {e}")
        return None


def check_chromadb_available() -> bool:
    """Check if chromadb package is installed."""
    try:
        import chromadb
        return True
    except ImportError:
        return False


# =============================================================================
# FILE HANDLING
# =============================================================================


def read_uploaded_file(uploaded_file) -> Optional[str]:
    """Read content from uploaded file."""
    try:
        filename = uploaded_file.name.lower()

        if filename.endswith('.txt') or filename.endswith('.md'):
            return uploaded_file.read().decode('utf-8')

        elif filename.endswith('.pdf'):
            try:
                import pypdf
                from io import BytesIO

                pdf_reader = pypdf.PdfReader(BytesIO(uploaded_file.read()))
                text_parts = []
                for page in pdf_reader.pages:
                    text_parts.append(page.extract_text() or "")
                return "\n\n".join(text_parts)
            except ImportError:
                st.error("pypdf not installed. Install with: pip install pypdf")
                return None

        elif filename.endswith('.json'):
            import json
            content = json.loads(uploaded_file.read().decode('utf-8'))
            return json.dumps(content, indent=2)

        elif filename.endswith('.csv'):
            import pandas as pd
            from io import StringIO

            content = uploaded_file.read().decode('utf-8')
            df = pd.read_csv(StringIO(content))
            # Convert to markdown table for better chunking
            return df.to_markdown(index=False)

        else:
            # Try to read as text
            return uploaded_file.read().decode('utf-8')

    except Exception as e:
        st.error(f"Error reading file: {e}")
        return None


# =============================================================================
# UI COMPONENTS
# =============================================================================


def render_hero():
    """Render the hero section."""
    st.markdown(
        """
        <div class="kb-hero">
            <h1>📚 Knowledge Base</h1>
            <p>Upload, vectorize, and search your documents with AI-powered semantic search</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_card(value: Any, label: str):
    """Render a statistics card."""
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{value}</div>
            <div class="stat-label">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_connection_status(connected: bool, url: str):
    """Render ChromaDB connection status."""
    if connected:
        st.markdown(
            f"""
            <div class="connection-status connected">
                <span>●</span> Connected to ChromaDB
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="connection-status disconnected">
                <span>●</span> Not connected - {url}
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_stats_section(client):
    """Render collection statistics."""
    stats = client.get_collection_stats()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        render_stat_card(stats.get("total_documents", 0), "Documents")
    with col2:
        render_stat_card(stats.get("total_chunks", 0), "Chunks")
    with col3:
        render_stat_card(stats["collection_name"], "Collection")
    with col4:
        status = "OK" if stats.get("connected") else "Error"
        render_stat_card(status, "Status")


def render_upload_section(client):
    """Render document upload interface."""
    st.markdown("### 📤 Upload Documents")

    col1, col2 = st.columns([2, 1])

    with col1:
        uploaded_files = st.file_uploader(
            "Choose files to upload",
            type=["txt", "md", "pdf", "json", "csv"],
            accept_multiple_files=True,
            help="Supported formats: TXT, Markdown, PDF, JSON, CSV",
        )

    with col2:
        st.markdown("**Chunking Settings**")
        chunk_size = st.slider("Chunk size", 200, 2000, 1000, 100)
        chunk_overlap = st.slider("Overlap", 0, 500, 200, 50)
        strategy = st.selectbox(
            "Strategy",
            options=["fixed", "sentence", "paragraph", "semantic"],
            index=0,
        )

    if uploaded_files:
        st.markdown("---")
        st.markdown(f"**{len(uploaded_files)} file(s) selected**")

        for uf in uploaded_files:
            with st.expander(f"📄 {uf.name} ({uf.size / 1024:.1f} KB)"):
                content = read_uploaded_file(uf)
                if content:
                    st.text_area(
                        "Preview",
                        content[:2000] + ("..." if len(content) > 2000 else ""),
                        height=150,
                        disabled=True,
                    )

        if st.button("📥 Upload All", type="primary", use_container_width=True):
            progress = st.progress(0)
            status_text = st.empty()

            success_count = 0
            for i, uf in enumerate(uploaded_files):
                status_text.text(f"Processing {uf.name}...")
                progress.progress((i + 1) / len(uploaded_files))

                # Reset file position
                uf.seek(0)
                content = read_uploaded_file(uf)

                if content:
                    try:
                        doc = client.add_document(
                            content=content,
                            filename=uf.name,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                            strategy=strategy,
                        )
                        success_count += 1
                        st.success(f"Uploaded {uf.name}: {doc.chunk_count} chunks created")
                    except Exception as e:
                        st.error(f"Failed to upload {uf.name}: {e}")

            status_text.text(f"Completed: {success_count}/{len(uploaded_files)} files uploaded")
            progress.progress(1.0)

            if success_count > 0:
                st.rerun()


def render_documents_section(client):
    """Render document management interface."""
    st.markdown("### 📁 Documents")

    docs = client.list_documents()

    if not docs:
        st.info("No documents in the knowledge base. Upload some documents to get started!")
        return

    # Search/filter
    search_filter = st.text_input("Filter documents", placeholder="Type to filter...")

    filtered_docs = docs
    if search_filter:
        search_lower = search_filter.lower()
        filtered_docs = [d for d in docs if search_lower in d.get("filename", "").lower()]

    st.caption(f"Showing {len(filtered_docs)} of {len(docs)} documents")

    for doc in filtered_docs:
        col1, col2, col3 = st.columns([3, 1, 1])

        with col1:
            st.markdown(
                f"""
                <div class="doc-card">
                    <div class="doc-title">📄 {doc.get('filename', 'Unknown')}</div>
                    <div class="doc-meta">
                        ID: {doc.get('id', 'N/A')[:8]}... |
                        {doc.get('chunk_count', 0)} chunks |
                        Added: {doc.get('created_at', 'N/A')[:10]}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col2:
            pass  # Placeholder for future actions

        with col3:
            if st.button("🗑️", key=f"del_{doc.get('id')}", help="Delete document"):
                if client.delete_document(doc.get("id")):
                    st.success(f"Deleted {doc.get('filename')}")
                    st.rerun()
                else:
                    st.error("Failed to delete document")


def render_search_section(client):
    """Render search interface."""
    st.markdown("### 🔍 Search")

    col1, col2 = st.columns([3, 1])

    with col1:
        query = st.text_input(
            "Search query",
            placeholder="Enter your search query...",
            label_visibility="collapsed",
        )

    with col2:
        n_results = st.selectbox("Results", options=[3, 5, 10, 20], index=1)

    # Optional: Filter by document
    docs = client.list_documents()
    doc_options = ["All documents"] + [d.get("filename", "Unknown") for d in docs]
    selected_doc = st.selectbox("Filter by document", options=doc_options, index=0)

    document_ids = None
    if selected_doc != "All documents":
        matching_docs = [d for d in docs if d.get("filename") == selected_doc]
        if matching_docs:
            document_ids = [matching_docs[0].get("id")]

    if st.button("🔍 Search", type="primary") and query:
        with st.spinner("Searching..."):
            try:
                results = client.search(
                    query=query,
                    n_results=n_results,
                    document_ids=document_ids,
                )

                if not results:
                    st.info("No results found. Try a different query.")
                else:
                    st.markdown(f"**Found {len(results)} results**")

                    for i, result in enumerate(results):
                        relevance_pct = int(result.relevance_score * 100)

                        st.markdown(
                            f"""
                            <div class="search-result">
                                <div class="search-result-header">
                                    <span class="relevance-badge">{relevance_pct}% relevant</span>
                                    📄 {result.source} (chunk {result.chunk_index})
                                </div>
                                <div class="search-result-text">
                                    {result.chunk_text[:500]}{'...' if len(result.chunk_text) > 500 else ''}
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

            except Exception as e:
                st.error(f"Search failed: {e}")


def render_settings_section(client):
    """Render settings and management interface."""
    st.markdown("### ⚙️ Settings")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Connection Info**")
        info = client.get_connection_info()
        st.json(info)

    with col2:
        st.markdown("**Collection Stats**")
        stats = client.get_collection_stats()
        st.json(stats)

    st.markdown("---")
    st.markdown("**Danger Zone**")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🔄 Refresh Connection", use_container_width=True):
            st.rerun()

    with col2:
        pass  # Reserved

    with col3:
        if st.button("🗑️ Reset Collection", type="secondary", use_container_width=True):
            if st.session_state.get("confirm_reset"):
                if client.reset_collection():
                    st.success("Collection reset successfully!")
                    st.session_state.confirm_reset = False
                    st.rerun()
                else:
                    st.error("Failed to reset collection")
            else:
                st.session_state.confirm_reset = True
                st.warning("Click again to confirm deletion of ALL documents!")


# =============================================================================
# MAIN PAGE
# =============================================================================


def main():
    render_hero()

    # Check ChromaDB availability
    if not check_chromadb_available():
        st.error(
            """
            **ChromaDB is not installed!**

            Install it with:
            ```
            pip install chromadb
            ```
            """
        )
        return

    # Get client
    client = get_kb_client_safe()

    if client is None:
        chromadb_url = os.getenv("CHROMADB_URL", "http://localhost:8000")
        st.warning(
            f"""
            **Cannot connect to ChromaDB at {chromadb_url}**

            Make sure ChromaDB is running. You can start it with:
            ```
            docker-compose up chromadb
            ```

            Or check your environment variables:
            - `CHROMADB_HOST` (default: localhost)
            - `CHROMADB_PORT` (default: 8000)
            """
        )
        return

    # Connection status
    info = client.get_connection_info()
    render_connection_status(info.get("connected", False), info.get("url", ""))

    if not info.get("connected"):
        st.stop()

    # Stats overview
    render_stats_section(client)

    st.divider()

    # Main tabs
    tab_upload, tab_docs, tab_search, tab_settings = st.tabs([
        "📤 Upload",
        "📁 Documents",
        "🔍 Search",
        "⚙️ Settings",
    ])

    with tab_upload:
        render_upload_section(client)

    with tab_docs:
        render_documents_section(client)

    with tab_search:
        render_search_section(client)

    with tab_settings:
        render_settings_section(client)

    # Footer
    st.divider()
    st.caption(
        f"📚 Knowledge Base | Collection: **{client.collection_name}** | "
        f"Connected to `{info.get('url', 'N/A')}` | "
        f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}"
    )


if __name__ == "__main__":
    main()
else:
    main()
