"""Text chunking utilities for Knowledge Base.

Provides various strategies for splitting documents into chunks
suitable for embedding and vector search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TextChunk:
    """A chunk of text with metadata."""

    text: str
    start_index: int
    end_index: int
    chunk_index: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TextChunker:
    """Configurable text chunker.

    Supports multiple chunking strategies:
    - fixed: Fixed-size chunks with overlap
    - sentence: Split by sentences
    - paragraph: Split by paragraphs
    - semantic: Split by semantic boundaries (headers, sections)
    """

    chunk_size: int = 1000
    chunk_overlap: int = 200
    strategy: str = "fixed"
    separators: List[str] = field(default_factory=lambda: ["\n\n", "\n", ". ", " ", ""])

    def chunk(self, text: str) -> List[TextChunk]:
        """Split text into chunks based on the configured strategy."""
        if not text or not text.strip():
            return []

        if self.strategy == "fixed":
            return self._chunk_fixed(text)
        elif self.strategy == "sentence":
            return self._chunk_sentence(text)
        elif self.strategy == "paragraph":
            return self._chunk_paragraph(text)
        elif self.strategy == "semantic":
            return self._chunk_semantic(text)
        else:
            return self._chunk_fixed(text)

    def _chunk_fixed(self, text: str) -> List[TextChunk]:
        """Fixed-size chunking with overlap."""
        chunks = []
        start = 0
        chunk_idx = 0

        while start < len(text):
            end = start + self.chunk_size

            # Try to find a good break point
            if end < len(text):
                for sep in self.separators:
                    if not sep:
                        continue
                    # Look for separator near the end
                    search_start = max(start + self.chunk_size - 100, start)
                    search_end = min(start + self.chunk_size + 50, len(text))
                    search_text = text[search_start:search_end]
                    sep_idx = search_text.rfind(sep)
                    if sep_idx > 0:
                        end = search_start + sep_idx + len(sep)
                        break

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(TextChunk(
                    text=chunk_text,
                    start_index=start,
                    end_index=end,
                    chunk_index=chunk_idx,
                ))
                chunk_idx += 1

            # Move start with overlap
            start = end - self.chunk_overlap
            if start <= chunks[-1].start_index if chunks else 0:
                start = end

        return chunks

    def _chunk_sentence(self, text: str) -> List[TextChunk]:
        """Split by sentences, grouping until chunk_size is reached."""
        # Simple sentence splitting
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_idx = 0
        start_idx = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_len = len(sentence)

            if current_size + sentence_len > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = " ".join(current_chunk)
                end_idx = start_idx + len(chunk_text)
                chunks.append(TextChunk(
                    text=chunk_text,
                    start_index=start_idx,
                    end_index=end_idx,
                    chunk_index=chunk_idx,
                ))
                chunk_idx += 1

                # Start new chunk with overlap (keep last few sentences)
                overlap_sentences = []
                overlap_size = 0
                for s in reversed(current_chunk):
                    if overlap_size + len(s) < self.chunk_overlap:
                        overlap_sentences.insert(0, s)
                        overlap_size += len(s)
                    else:
                        break

                current_chunk = overlap_sentences
                current_size = overlap_size
                start_idx = end_idx - overlap_size

            current_chunk.append(sentence)
            current_size += sentence_len

        # Add remaining chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append(TextChunk(
                text=chunk_text,
                start_index=start_idx,
                end_index=start_idx + len(chunk_text),
                chunk_index=chunk_idx,
            ))

        return chunks

    def _chunk_paragraph(self, text: str) -> List[TextChunk]:
        """Split by paragraphs."""
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_idx = 0
        start_idx = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_len = len(para)

            if current_size + para_len > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = "\n\n".join(current_chunk)
                end_idx = start_idx + len(chunk_text)
                chunks.append(TextChunk(
                    text=chunk_text,
                    start_index=start_idx,
                    end_index=end_idx,
                    chunk_index=chunk_idx,
                ))
                chunk_idx += 1
                current_chunk = []
                current_size = 0
                start_idx = end_idx + 2  # Account for \n\n

            current_chunk.append(para)
            current_size += para_len

        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append(TextChunk(
                text=chunk_text,
                start_index=start_idx,
                end_index=start_idx + len(chunk_text),
                chunk_index=chunk_idx,
            ))

        return chunks

    def _chunk_semantic(self, text: str) -> List[TextChunk]:
        """Split by semantic boundaries (headers, sections)."""
        # Split on markdown headers or horizontal rules
        header_pattern = r'^(#{1,6}\s+.+|[-=]{3,})$'
        lines = text.split('\n')

        sections = []
        current_section = []
        current_header = None

        for line in lines:
            if re.match(header_pattern, line.strip(), re.MULTILINE):
                if current_section:
                    sections.append((current_header, '\n'.join(current_section)))
                current_header = line.strip()
                current_section = []
            else:
                current_section.append(line)

        if current_section:
            sections.append((current_header, '\n'.join(current_section)))

        # Now chunk sections
        chunks = []
        chunk_idx = 0
        position = 0

        for header, content in sections:
            section_text = f"{header}\n{content}" if header else content
            section_text = section_text.strip()

            if not section_text:
                continue

            # If section is small enough, keep it as one chunk
            if len(section_text) <= self.chunk_size:
                chunks.append(TextChunk(
                    text=section_text,
                    start_index=position,
                    end_index=position + len(section_text),
                    chunk_index=chunk_idx,
                    metadata={"header": header} if header else {},
                ))
                chunk_idx += 1
            else:
                # Split large sections using fixed chunking
                sub_chunker = TextChunker(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    strategy="fixed",
                )
                sub_chunks = sub_chunker.chunk(section_text)
                for sub in sub_chunks:
                    sub.chunk_index = chunk_idx
                    sub.start_index += position
                    sub.end_index += position
                    if header:
                        sub.metadata["header"] = header
                    chunks.append(sub)
                    chunk_idx += 1

            position += len(section_text) + 2  # Account for section separator

        return chunks


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    strategy: str = "fixed",
) -> List[str]:
    """Convenience function to chunk text and return just the text strings.

    Args:
        text: Text to chunk
        chunk_size: Maximum chunk size in characters
        chunk_overlap: Overlap between chunks
        strategy: Chunking strategy ("fixed", "sentence", "paragraph", "semantic")

    Returns:
        List of chunk texts
    """
    chunker = TextChunker(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=strategy,
    )
    chunks = chunker.chunk(text)
    return [c.text for c in chunks]


def chunk_document(
    content: str,
    filename: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    strategy: str = "fixed",
) -> List[Tuple[str, Dict[str, Any]]]:
    """Chunk a document and return chunks with metadata.

    Args:
        content: Document content
        filename: Original filename
        chunk_size: Maximum chunk size
        chunk_overlap: Overlap between chunks
        strategy: Chunking strategy

    Returns:
        List of (chunk_text, metadata) tuples
    """
    chunker = TextChunker(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=strategy,
    )
    chunks = chunker.chunk(content)

    result = []
    for chunk in chunks:
        metadata = {
            "source": filename,
            "chunk_index": chunk.chunk_index,
            "start_index": chunk.start_index,
            "end_index": chunk.end_index,
            **chunk.metadata,
        }
        result.append((chunk.text, metadata))

    return result
