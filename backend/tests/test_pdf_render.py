"""PDF render helpers: zoom clamp for oversized pages."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.services.pdf import MAX_RENDER_PIXELS, clamp_render_zoom, get_page_png


def test_clamp_render_zoom_unchanged_for_normal_page():
    # A4-ish page at default zoom stays within the cap.
    assert clamp_render_zoom(595, 842, 1.5) == 1.5


def test_clamp_render_zoom_reduces_for_huge_page():
    zoom = clamp_render_zoom(20_000, 20_000, 2.0)
    assert zoom < 2.0
    assert 20_000 * 20_000 * zoom * zoom <= MAX_RENDER_PIXELS + 1e-6


def test_get_page_png_clamps_huge_page(tmp_path: Path):
    path = tmp_path / "huge-page.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=12_000, height=12_000)
    page.insert_text((100, 100), "oversized-page-marker", fontsize=24)
    doc.save(path)
    doc.close()

    # Unclamped zoom 2.0 would be 12k*12k*4 = 576M pixels.
    png = get_page_png(path, 1, zoom=2.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    rendered = pymupdf.Pixmap(png)
    try:
        assert rendered.width * rendered.height <= MAX_RENDER_PIXELS
        # Still produced a usable preview, not a tiny stub.
        assert rendered.width >= 100
        assert rendered.height >= 100
    finally:
        rendered = None
