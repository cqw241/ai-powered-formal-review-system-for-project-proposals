"""PDF open / page text / page image helpers (PyMuPDF)."""

from __future__ import annotations

from pathlib import Path

import pymupdf


class PdfError(Exception):
    """Raised when a PDF cannot be opened or a page cannot be read."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def open_document(path: Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 — surface as PdfError
        raise PdfError(f"无法打开 PDF：{exc}") from exc
    if doc.is_encrypted:
        doc.close()
        raise PdfError("不支持加密的 PDF")
    if doc.page_count < 1:
        doc.close()
        raise PdfError("PDF 没有可读取的页面")
    return doc


def inspect_pdf(path: Path) -> int:
    """Return page count; raises PdfError if unreadable."""
    doc = open_document(path)
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def get_page_text(path: Path, page_number: int) -> str:
    """Extract native text for a 1-based page number."""
    doc = open_document(path)
    try:
        _ensure_page_in_range(doc, page_number)
        page = doc.load_page(page_number - 1)
        return page.get_text() or ""
    finally:
        doc.close()


def get_page_png(path: Path, page_number: int, *, zoom: float = 1.5) -> bytes:
    """Render a 1-based page to PNG bytes."""
    doc = open_document(path)
    try:
        _ensure_page_in_range(doc, page_number)
        page = doc.load_page(page_number - 1)
        matrix = pymupdf.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def _ensure_page_in_range(doc: pymupdf.Document, page_number: int) -> None:
    if page_number < 1 or page_number > doc.page_count:
        raise PdfError(f"页码超出范围：有效范围为 1–{doc.page_count}，收到 {page_number}")
