"""PDF open / page text / page image helpers (PyMuPDF)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

# Cap rendered bitmap size to avoid huge memory spikes on oversized pages.
MAX_RENDER_PIXELS = 16_777_216  # 4096 * 4096
MIN_RENDER_ZOOM = 0.25
_ABS_MIN_ZOOM = 1e-3
_MAX_RENDER_ATTEMPTS = 8


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
    """Render a 1-based page to PNG bytes.

    Zoom is first estimated from page rect, then verified against the actual
    pixmap size. If the rendered pixel count still exceeds MAX_RENDER_PIXELS,
    zoom is reduced and the page is re-rendered.
    """
    doc = open_document(path)
    try:
        _ensure_page_in_range(doc, page_number)
        page = doc.load_page(page_number - 1)
        effective_zoom = clamp_render_zoom(page.rect.width, page.rect.height, zoom)

        last_pixels = 0
        for _ in range(_MAX_RENDER_ATTEMPTS):
            matrix = pymupdf.Matrix(effective_zoom, effective_zoom)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            last_pixels = int(pixmap.width) * int(pixmap.height)
            if last_pixels <= MAX_RENDER_PIXELS:
                return pixmap.tobytes("png")

            # Actual raster exceeded the estimate (rounding / media box). Shrink and retry.
            shrink = (MAX_RENDER_PIXELS / last_pixels) ** 0.5
            next_zoom = effective_zoom * shrink * 0.98
            if next_zoom >= effective_zoom:
                next_zoom = effective_zoom * 0.9
            effective_zoom = max(next_zoom, _ABS_MIN_ZOOM)
            if effective_zoom <= _ABS_MIN_ZOOM + 1e-12:
                break

        raise PdfError(
            f"页面过大，无法在 {MAX_RENDER_PIXELS} 像素上限内渲染（最近一次 {last_pixels} 像素）"
        )
    finally:
        doc.close()


def clamp_render_zoom(page_width: float, page_height: float, zoom: float) -> float:
    """Estimate zoom so that width*height*zoom^2 does not exceed MAX_RENDER_PIXELS."""
    if zoom <= 0:
        zoom = MIN_RENDER_ZOOM
    width = abs(float(page_width))
    height = abs(float(page_height))
    if width <= 0 or height <= 0:
        return max(zoom, MIN_RENDER_ZOOM)

    area = width * height
    max_pixels = float(MAX_RENDER_PIXELS)
    if area * zoom * zoom <= max_pixels:
        return zoom

    # Refuse before get_pixmap when even the theoretical safe zoom is unusable.
    limited = (max_pixels / area) ** 0.5
    if limited < _ABS_MIN_ZOOM:
        raise PdfError("页面尺寸过大，无法安全渲染")
    return limited


def _ensure_page_in_range(doc: pymupdf.Document, page_number: int) -> None:
    if page_number < 1 or page_number > doc.page_count:
        raise PdfError(f"页码超出范围：有效范围为 1–{doc.page_count}，收到 {page_number}")
