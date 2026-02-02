"""ChromaDB client for Knowledge Base operations.

Provides connection management, document storage, and vector search
functionality using ChromaDB as the backend.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    chromadb = None
    Settings = None
    CHROMADB_AVAILABLE = False

from src.knowledge_base.chunker import chunk_document


@dataclass
class KBDocument:
    """Represents a document in the knowledge base."""

    id: str
    content: str
    filename: str
    chunk_count: int
    created_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KBDocument":
        """Create a KBDocument from a dictionary."""
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            filename=data.get("filename", ""),
            chunk_count=data.get("chunk_count", 0),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "filename": self.filename,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class KBSearchResult:
    """A single search result from the knowledge base."""

    chunk_text: str
    document_id: str
    source: str
    chunk_index: int
    distance: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def relevance_score(self) -> float:
        """Convert distance to a relevance score (0-1, higher is better)."""
        # ChromaDB uses L2 distance by default
        # Convert to relevance: 1 / (1 + distance)
        return 1 / (1 + self.distance)


class KnowledgeBaseClient:
    """Client for interacting with the Knowledge Base ChromaDB backend.

    Handles:
    - Connection management to ChromaDB
    - Document storage with automatic chunking
    - Vector search functionality
    - Collection management
    """

    DEFAULT_COLLECTION = "knowledge_base"

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        collection_name: Optional[str] = None,
    ):
        """Initialize the Knowledge Base client.

        Args:
            host: ChromaDB host (defaults to CHROMADB_HOST env var or localhost)
            port: ChromaDB port (defaults to CHROMADB_PORT env var or 8000)
            collection_name: Collection to use (defaults to "knowledge_base")
        """
        if not CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb is not installed. Install it with: pip install chromadb"
            )

        self.host = host or os.getenv("CHROMADB_HOST", "localhost")
        self.port = int(port or os.getenv("CHROMADB_PORT", "8000"))
        self.collection_name = collection_name or self.DEFAULT_COLLECTION

        self._client: Optional[chromadb.HttpClient] = None
        self._collection = None

    @property
    def client(self) -> chromadb.HttpClient:
        """Get or create the ChromaDB client connection."""
        if self._client is None:
            self._client = chromadb.HttpClient(
                host=self.host,
                port=self.port,
                settings=Settings(anonymized_telemetry=False),
            )
        return self._client

    @property
    def collection(self):
        """Get or create the collection."""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Knowledge Base documents"},
            )
        return self._collection

    def is_connected(self) -> bool:
        """Check if connected to ChromaDB."""
        try:
            self.client.heartbeat()
            return True
        except Exception:
            return False

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "collection": self.collection_name,
            "connected": self.is_connected(),
            "url": f"http://{self.host}:{self.port}",
        }

    def _generate_doc_id(self, content: str, filename: str) -> str:
        """Generate a unique document ID based on content hash."""
        hash_input = f"{filename}:{content[:1000]}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def add_document(
        self,
        content: str,
        filename: str,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        strategy: str = "fixed",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KBDocument:
        """Add a document to the knowledge base.

        The document is automatically chunked and each chunk is stored
        as a separate vector in ChromaDB.

        Args:
            content: The document content
            filename: Original filename
            chunk_size: Size of each chunk in characters
            chunk_overlap: Overlap between chunks
            strategy: Chunking strategy ("fixed", "sentence", "paragraph", "semantic")
            metadata: Additional metadata to store with the document

        Returns:
            KBDocument with the stored document info
        """
        doc_id = self._generate_doc_id(content, filename)
        created_at = datetime.utcnow().isoformat() + "Z"

        # Chunk the document
        chunks = chunk_document(
            content=content,
            filename=filename,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            strategy=strategy,
        )

        if not chunks:
            raise ValueError("Document produced no chunks. Is it empty?")

        # Prepare data for ChromaDB
        ids = []
        documents = []
        metadatas = []

        base_metadata = metadata or {}

        for chunk_text, chunk_meta in chunks:
            chunk_id = f"{doc_id}_{chunk_meta['chunk_index']}"
            ids.append(chunk_id)
            documents.append(chunk_text)
            metadatas.append({
                **base_metadata,
                **chunk_meta,
                "document_id": doc_id,
                "filename": filename,
                "created_at": created_at,
            })

        # Store in ChromaDB (embeddings are generated automatically)
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )

        return KBDocument(
            id=doc_id,
            content=content[:500] + "..." if len(content) > 500 else content,
            filename=filename,
            chunk_count=len(chunks),
            created_at=created_at,
            metadata=base_metadata,
        )

    def search(
        self,
        query: str,
        n_results: int = 5,
        *,
        document_ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[KBSearchResult]:
        """Search the knowledge base for relevant chunks.

        Args:
            query: The search query
            n_results: Maximum number of results to return
            document_ids: Optional list of document IDs to search within
            where: Optional ChromaDB where clause for filtering

        Returns:
            List of KBSearchResult objects sorted by relevance
        """
        # Build where clause
        where_clause = where or {}
        if document_ids:
            if len(document_ids) == 1:
                where_clause["document_id"] = document_ids[0]
            else:
                where_clause["document_id"] = {"$in": document_ids}

        # Perform search
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause if where_clause else None,
            include=["documents", "metadatas", "distances"],
        )

        # Convert to KBSearchResult objects
        search_results = []
        if results and results["documents"] and results["documents"][0]:
            documents = results["documents"][0]
            metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(documents)
            distances = results["distances"][0] if results["distances"] else [0.0] * len(documents)

            for doc, meta, dist in zip(documents, metadatas, distances):
                search_results.append(KBSearchResult(
                    chunk_text=doc,
                    document_id=meta.get("document_id", ""),
                    source=meta.get("source", meta.get("filename", "")),
                    chunk_index=meta.get("chunk_index", 0),
                    distance=dist,
                    metadata=meta,
                ))

        return search_results

    def delete_document(self, document_id: str) -> bool:
        """Delete a document and all its chunks from the knowledge base.

        Args:
            document_id: The document ID to delete

        Returns:
            True if deletion was successful
        """
        try:
            # Delete all chunks with this document_id
            self.collection.delete(
                where={"document_id": document_id}
            )
            return True
        except Exception:
            return False

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents in the knowledge base.

        Returns:
            List of document summaries with id, filename, chunk_count
        """
        # Get all items from collection
        results = self.collection.get(
            include=["metadatas"],
        )

        if not results or not results["metadatas"]:
            return []

        # Group by document_id
        docs: Dict[str, Dict[str, Any]] = {}
        for meta in results["metadatas"]:
            doc_id = meta.get("document_id", "")
            if doc_id not in docs:
                docs[doc_id] = {
                    "id": doc_id,
                    "filename": meta.get("filename", meta.get("source", "unknown")),
                    "created_at": meta.get("created_at", ""),
                    "chunk_count": 0,
                }
            docs[doc_id]["chunk_count"] += 1

        return list(docs.values())

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the knowledge base collection.

        Returns:
            Dict with count, documents, and other stats
        """
        try:
            count = self.collection.count()
            docs = self.list_documents()
            return {
                "total_chunks": count,
                "total_documents": len(docs),
                "collection_name": self.collection_name,
                "connected": True,
            }
        except Exception as e:
            return {
                "total_chunks": 0,
                "total_documents": 0,
                "collection_name": self.collection_name,
                "connected": False,
                "error": str(e),
            }

    def reset_collection(self) -> bool:
        """Delete and recreate the collection.

        WARNING: This deletes all data in the collection!

        Returns:
            True if reset was successful
        """
        try:
            self.client.delete_collection(self.collection_name)
            self._collection = None
            # Recreate by accessing property
            _ = self.collection
            return True
        except Exception:
            return False


# Singleton instance
_kb_client: Optional[KnowledgeBaseClient] = None


def get_kb_client(
    host: Optional[str] = None,
    port: Optional[int] = None,
    collection_name: Optional[str] = None,
    *,
    force_new: bool = False,
) -> KnowledgeBaseClient:
    """Get or create a Knowledge Base client instance.

    This function provides a singleton pattern for the KB client,
    reusing the same connection across multiple calls.

    Args:
        host: ChromaDB host (defaults to env var or localhost)
        port: ChromaDB port (defaults to env var or 8000)
        collection_name: Collection to use
        force_new: If True, create a new client even if one exists

    Returns:
        KnowledgeBaseClient instance
    """
    global _kb_client

    if force_new or _kb_client is None:
        _kb_client = KnowledgeBaseClient(
            host=host,
            port=port,
            collection_name=collection_name,
        )

    return _kb_client
