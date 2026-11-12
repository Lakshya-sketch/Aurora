"""Document Loader and Recursive Text Chunker — Phase 4.2 Vector RAG.

Provides multi-format document text extraction and semantic recursive chunking:
1. Multi-format loading:
   - PDF files (via PyPDF2) with per-page tracking
   - Markdown (.md, .markdown)
   - Plain text (.txt, .rst)
   - Code & Structured data (.json, .csv, .py, .js, .html, etc.)
2. Recursive Text Chunker:
   - Splits on natural document boundaries (paragraphs -> lines -> sentences -> words)
   - Configurable chunk size and overlap to preserve semantic context across chunks
   - Generates deterministic chunk metadata (source, filename, page, start/end character offsets)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Supported document extensions
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".csv",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".yaml", ".yml"
}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | DOCX_EXTENSIONS | TEXT_EXTENSIONS

# Directories to skip when scanning folders
IGNORED_DIRS = {
    ".git", "venv", ".venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", "dist", "build", ".hermes", ".gemini"
}


@dataclass
class DocumentPage:
    """Represents text extracted from a specific page or section of a document."""
    text: str
    page_number: int
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentChunk:
    """Represents an indexed chunk ready for embedding and vector search."""
    chunk_id: str
    text: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": self.metadata,
        }


# ----------------------------------------------------------------------
# 1. Document Loading
# ----------------------------------------------------------------------

def _read_text_file(file_path: Path) -> str:
    """Read a text-based file trying utf-8 first, falling back to latin-1."""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="latin-1", errors="replace")


def _load_pdf(file_path: Path) -> list[DocumentPage]:
    """Extract text from a PDF file page by page using PyPDF2."""
    import PyPDF2

    pages: list[DocumentPage] = []
    source_str = str(file_path)

    try:
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as exc:
                    return [DocumentPage(
                        text=f"[Error: PDF is encrypted/password-protected: {exc}]",
                        page_number=1,
                        source=source_str,
                        metadata={"error": "encrypted"}
                    )]

            total_pages = len(reader.pages)
            for idx, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as exc:
                    text = f"[Error extracting text from page {idx}: {exc}]"

                cleaned_text = text.strip()
                if cleaned_text:
                    pages.append(DocumentPage(
                        text=cleaned_text,
                        page_number=idx,
                        source=source_str,
                        metadata={
                            "total_pages": total_pages,
                            "filename": file_path.name,
                        }
                    ))
    except Exception as exc:
        pages.append(DocumentPage(
            text=f"[Error opening PDF {file_path.name}: {exc}]",
            page_number=1,
            source=source_str,
            metadata={"error": str(exc), "filename": file_path.name}
        ))

    return pages


def _load_docx(file_path: Path) -> list[DocumentPage]:
    """Extract text from a Microsoft Word (.docx) file using built-in zipfile and XML parsing."""
    import zipfile
    import xml.etree.ElementTree as ET

    source_str = str(file_path)
    try:
        with zipfile.ZipFile(file_path, "r") as docx_zip:
            xml_content = docx_zip.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

            paragraphs: list[str] = []
            for p in tree.iterfind(".//w:p", namespaces):
                texts = [node.text for node in p.iterfind(".//w:t", namespaces) if node.text]
                if texts:
                    paragraph_text = "".join(texts).strip()
                    if paragraph_text:
                        paragraphs.append(paragraph_text)

            full_text = "\n\n".join(paragraphs).strip()
            if not full_text:
                return []

            return [DocumentPage(
                text=full_text,
                page_number=1,
                source=source_str,
                metadata={
                    "total_pages": 1,
                    "filename": file_path.name,
                    "format": "docx",
                    "total_paragraphs": len(paragraphs),
                }
            )]
    except Exception as exc:
        return [DocumentPage(
            text=f"[Error opening DOCX {file_path.name}: {exc}]",
            page_number=1,
            source=source_str,
            metadata={"error": str(exc), "filename": file_path.name, "format": "docx"}
        )]


def load_document(file_path: str | Path) -> list[DocumentPage]:
    """Load and extract text from a single file (PDF, Word DOCX, Markdown, TXT, Code).

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        List of DocumentPage objects containing page text and metadata.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Document file not found: {path}")

    ext = path.suffix.lower()

    if ext in PDF_EXTENSIONS:
        return _load_pdf(path)
    elif ext in DOCX_EXTENSIONS:
        return _load_docx(path)
    elif ext in TEXT_EXTENSIONS or ext == "":
        text = _read_text_file(path).strip()
        if not text:
            return []
        return [DocumentPage(
            text=text,
            page_number=1,
            source=str(path),
            metadata={"filename": path.name, "total_pages": 1}
        )]
    else:
        # Fallback: attempt text read
        try:
            text = _read_text_file(path).strip()
            if not text:
                return []
            return [DocumentPage(
                text=text,
                page_number=1,
                source=str(path),
                metadata={"filename": path.name, "total_pages": 1}
            )]
        except Exception as exc:
            raise ValueError(f"Unsupported file format '{ext}' for document loader: {exc}")


def load_directory(
    dir_path: str | Path,
    recursive: bool = True,
    extensions: set[str] | None = None,
) -> list[DocumentPage]:
    """Load all supported documents from a directory.

    Args:
        dir_path: Directory path to scan.
        recursive: Whether to scan subdirectories. Defaults to True.
        extensions: Optional set of allowed extensions (e.g. {'.pdf', '.md'}).

    Returns:
        Consolidated list of DocumentPage objects from all loaded files.
    """
    target = Path(dir_path).resolve()
    if not target.is_dir():
        raise NotADirectoryError(f"Directory not found: {target}")

    allowed = {e.lower() for e in extensions} if extensions else SUPPORTED_EXTENSIONS
    all_pages: list[DocumentPage] = []

    for root, dirs, files in os.walk(target):
        # Exclude ignored directories in-place
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in allowed:
                fpath = Path(root) / f
                try:
                    pages = load_document(fpath)
                    all_pages.extend(pages)
                except Exception:
                    continue

        if not recursive:
            break

    return all_pages


# ----------------------------------------------------------------------
# 2. Recursive Text Chunker
# ----------------------------------------------------------------------

class RecursiveChunker:
    """Recursively splits text on natural boundaries with configurable overlap.

    Hierarchy of split points:
      1. Paragraphs (\\n\\n)
      2. Lines (\\n)
      3. Sentences (. , ? , ! )
      4. Words (spaces)
      5. Characters (fallback)
    """

    def __init__(
        self,
        chunk_size: int = 600,
        chunk_overlap: int = 100,
        separators: list[str] | None = None,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError(f"chunk_overlap ({chunk_overlap}) must be strictly less than chunk_size ({chunk_size})")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", ". ", "? ", "! ", " ", ""]

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """Split text recursively using the given list of separators."""
        final_splits: list[str] = []
        separator = separators[-1]
        new_separators: list[str] = []

        # Find the first separator present in the text
        for i, sep in enumerate(separators):
            if sep == "":
                separator = ""
                break
            if sep in text:
                separator = sep
                new_separators = separators[i + 1:]
                break

        # Split on the chosen separator
        if separator:
            splits = text.split(separator)
        else:
            splits = list(text)

        # Process each piece: if still too long, recurse on next separator
        for piece in splits:
            if not piece.strip():
                continue
            if len(piece) <= self.chunk_size:
                final_splits.append(piece)
            else:
                if new_separators:
                    other_splits = self._split_text(piece, new_separators)
                    final_splits.extend(other_splits)
                else:
                    # Hard character cut if no separators remain
                    for j in range(0, len(piece), self.chunk_size - self.chunk_overlap):
                        final_splits.append(piece[j: j + self.chunk_size])

        return final_splits

    def _merge_splits(self, splits: list[str]) -> list[str]:
        """Merge small splits up to chunk_size, applying chunk_overlap."""
        docs: list[str] = []
        current_doc: list[str] = []
        total_len = 0

        for split in splits:
            piece_len = len(split)
            if total_len + piece_len + (1 if current_doc else 0) > self.chunk_size:
                if current_doc:
                    doc = " ".join(current_doc).strip()
                    if doc:
                        docs.append(doc)

                    # Keep overlap from previous doc
                    while current_doc and total_len > self.chunk_overlap:
                        popped = current_doc.pop(0)
                        total_len -= len(popped) + 1

                current_doc.append(split)
                total_len = sum(len(p) + 1 for p in current_doc) - 1
            else:
                current_doc.append(split)
                total_len += piece_len + (1 if len(current_doc) > 1 else 0)

        if current_doc:
            doc = " ".join(current_doc).strip()
            if doc:
                docs.append(doc)

        return docs

    def split_text(self, text: str) -> list[str]:
        """Split a raw text string into chunks."""
        if not text or not text.strip():
            return []
        if len(text) <= self.chunk_size:
            return [text.strip()]

        splits = self._split_text(text, self.separators)
        return self._merge_splits(splits)


# ----------------------------------------------------------------------
# 3. High-Level Chunker APIs
# ----------------------------------------------------------------------

def chunk_text(
    text: str,
    chunk_size: int = 600,
    chunk_overlap: int = 100,
    source: str = "text",
    page: int = 1,
) -> list[DocumentChunk]:
    """Split raw text into structured DocumentChunk objects with metadata."""
    chunker = RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    raw_chunks = chunker.split_text(text)

    chunks: list[DocumentChunk] = []
    base_name = Path(source).name if source != "text" else "inline"

    char_cursor = 0
    for idx, c_text in enumerate(raw_chunks):
        c_len = len(c_text)
        # Approximate character offsets in original text
        found_idx = text.find(c_text[:30], char_cursor) if len(c_text) >= 30 else text.find(c_text, char_cursor)
        if found_idx != -1:
            char_start = found_idx
            char_cursor = char_start + 1
        else:
            char_start = char_cursor
        char_end = char_start + c_len

        chunk_id = f"{base_name}_p{page}_c{idx}"
        chunks.append(DocumentChunk(
            chunk_id=chunk_id,
            text=c_text,
            metadata={
                "source": source,
                "filename": base_name,
                "page": page,
                "chunk_index": idx,
                "char_start": char_start,
                "char_end": char_end,
                "char_count": c_len,
            }
        ))

    return chunks


def chunk_documents(
    pages: list[DocumentPage],
    chunk_size: int = 600,
    chunk_overlap: int = 100,
) -> list[DocumentChunk]:
    """Chunk a list of DocumentPage objects, preserving page numbers and file sources.

    Args:
        pages: List of DocumentPage objects returned by load_document or load_directory.
        chunk_size: Maximum character length per chunk (default 600).
        chunk_overlap: Overlap length between adjacent chunks (default 100).

    Returns:
        List of DocumentChunk objects ready for vector embedding.
    """
    all_chunks: list[DocumentChunk] = []
    for page in pages:
        chunks = chunk_text(
            text=page.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            source=page.source,
            page=page.page_number,
        )
        # Merge page-level metadata into chunk metadata
        for c in chunks:
            for k, v in page.metadata.items():
                if k not in c.metadata:
                    c.metadata[k] = v
        all_chunks.extend(chunks)

    return all_chunks
