"""Tests for Document Loader & Recursive Chunker (Phase 4.2 Vector RAG)."""

from __future__ import annotations

import io
from pathlib import Path
import pytest
import PyPDF2

from plugins.rag_chroma.document_loader import (
    DocumentPage,
    DocumentChunk,
    RecursiveChunker,
    load_document,
    load_directory,
    chunk_text,
    chunk_documents,
)


# ----------------------------------------------------------------------
# 1. Text & Markdown Loading Tests
# ----------------------------------------------------------------------

def test_load_text_file(tmp_path: Path):
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Hello Aurora! This is a test document.\nSecond line here.", encoding="utf-8")

    pages = load_document(txt_file)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "Hello Aurora!" in pages[0].text
    assert pages[0].metadata["filename"] == "notes.txt"


def test_load_markdown_file(tmp_path: Path):
    md_file = tmp_path / "readme.md"
    md_file.write_text("# Title\n\nThis is a markdown file with **bold** text.", encoding="utf-8")

    pages = load_document(md_file)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "# Title" in pages[0].text


def test_load_docx_file(tmp_path: Path):
    """Test loading and extracting text from a .docx file without external dependencies."""
    import zipfile
    docx_file = tmp_path / "sample.docx"
    xml_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>Introduction to Algorithms</w:t></w:r></w:p>
            <w:p><w:r><w:t>Chapter 1 covers asymptotic analysis and Big O notation.</w:t></w:r></w:p>
        </w:body>
    </w:document>
    """
    with zipfile.ZipFile(docx_file, "w") as z:
        z.writestr("word/document.xml", xml_content)

    pages = load_document(docx_file)
    assert len(pages) == 1
    assert "Introduction to Algorithms" in pages[0].text
    assert "asymptotic analysis" in pages[0].text
    assert pages[0].metadata["format"] == "docx"


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_document("does_not_exist_ever_12345.pdf")


# ----------------------------------------------------------------------
# 2. PDF Loading Tests
# ----------------------------------------------------------------------

def test_load_pdf_file(tmp_path: Path):
    """Create a synthetic PDF with PyPDF2 and test text extraction."""
    # Write a simple PDF using PyPDF2.PdfWriter
    pdf_path = tmp_path / "sample.pdf"
    writer = PyPDF2.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    # Empty blank page returns 0 extracted text pages or empty
    pages = load_document(pdf_path)
    assert isinstance(pages, list)


# ----------------------------------------------------------------------
# 3. Directory Loading Tests
# ----------------------------------------------------------------------

def test_load_directory(tmp_path: Path):
    # Create sample docs
    (tmp_path / "doc1.txt").write_text("Content of document 1", encoding="utf-8")
    (tmp_path / "doc2.md").write_text("Content of document 2", encoding="utf-8")
    
    # Subdir inside
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "doc3.txt").write_text("Content of document 3", encoding="utf-8")

    # Ignored directory
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    (venv_dir / "skip.txt").write_text("This should be skipped", encoding="utf-8")

    pages = load_directory(tmp_path, recursive=True)
    filenames = [p.metadata["filename"] for p in pages]
    assert "doc1.txt" in filenames
    assert "doc2.md" in filenames
    assert "doc3.txt" in filenames
    assert "skip.txt" not in filenames  # skipped venv


# ----------------------------------------------------------------------
# 4. RecursiveChunker Unit Tests
# ----------------------------------------------------------------------

def test_recursive_chunker_short_text():
    chunker = RecursiveChunker(chunk_size=500, chunk_overlap=50)
    text = "Short sentence that fits into one single chunk."
    chunks = chunker.split_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_recursive_chunker_splits_paragraphs():
    p1 = "Paragraph 1 is long enough to test. " * 10
    p2 = "Paragraph 2 is also long enough. " * 10
    full_text = f"{p1}\n\n{p2}"

    chunker = RecursiveChunker(chunk_size=200, chunk_overlap=40)
    chunks = chunker.split_text(full_text)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 300  # Within bounds


def test_chunk_overlap_validation():
    with pytest.raises(ValueError):
        RecursiveChunker(chunk_size=100, chunk_overlap=150)


# ----------------------------------------------------------------------
# 5. chunk_text & chunk_documents Tests
# ----------------------------------------------------------------------

def test_chunk_text_metadata():
    text = "First paragraph here.\n\nSecond paragraph has more text to split.\n\nThird paragraph."
    chunks = chunk_text(text, chunk_size=50, chunk_overlap=10, source="my_doc.txt", page=2)

    assert len(chunks) >= 2
    assert chunks[0].metadata["filename"] == "my_doc.txt"
    assert chunks[0].metadata["page"] == 2
    assert chunks[0].chunk_id.startswith("my_doc.txt_p2_c")


def test_chunk_documents():
    pages = [
        DocumentPage(text="Page 1 content " * 15, page_number=1, source="doc.pdf", metadata={"total_pages": 2}),
        DocumentPage(text="Page 2 content " * 15, page_number=2, source="doc.pdf", metadata={"total_pages": 2}),
    ]

    chunks = chunk_documents(pages, chunk_size=100, chunk_overlap=20)
    assert len(chunks) >= 2
    # Ensure page numbers are preserved across chunks
    pages_in_chunks = {c.metadata["page"] for c in chunks}
    assert 1 in pages_in_chunks
    assert 2 in pages_in_chunks
    assert all(c.metadata["total_pages"] == 2 for c in chunks)
