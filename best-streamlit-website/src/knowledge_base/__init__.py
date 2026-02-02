"""Knowledge Base module for document management and vector search.

This module provides utilities for:
- Uploading and managing documents
- Chunking text into smaller pieces
- Vectorizing text using embeddings
- Storing and searching in ChromaDB

Usage:
    from src.knowledge_base import KnowledgeBaseClient

    client = KnowledgeBaseClient()
    client.add_document("My document content", metadata={"source": "file.txt"})
    results = client.search("query text", n_results=5)
"""

from src.knowledge_base.client import (
    KnowledgeBaseClient,
    get_kb_client,
    KBDocument,
    KBSearchResult,
)
from src.knowledge_base.chunker import (
    TextChunker,
    chunk_text,
    chunk_document,
)

__all__ = [
    "KnowledgeBaseClient",
    "get_kb_client",
    "KBDocument",
    "KBSearchResult",
    "TextChunker",
    "chunk_text",
    "chunk_document",
]
