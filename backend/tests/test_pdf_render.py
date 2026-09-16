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


def test_get_page_png_actual_pixels_within_limit(tmp_path: Path):
    """Assert the returned PNG bitmap itself stays within MAX_RENDER_PIXELS."""
    path = tmp_path / "huge-page.pdf"
    doc = pymupdf.open()
    # Large square page: theoretical clamp can still undershoot due to raster rounding.
    page = doc.new_page(width=12_000, height=12_000)
    page.insert_text((100, 100), "oversized-page-marker", fontsize=24)
    doc.save(path)
    doc.close()

    # Unclamped zoom 2.0 would be 12k*12k*4 = 576M pixels.
    png = get_page_png(path, 1, zoom=2.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    rendered = pymupdf.Pixmap(png)
    try:
        actual_pixels = rendered.width * rendered.height
        assert actual_pixels <= MAX_RENDER_PIXELS
        # Still produced a usable preview, not a tiny stub.
        assert rendered.width >= 100
        assert rendered.height >= 100
    finally:
        rendered = None


def test_get_page_png_retries_when_estimate_is_tight(tmp_path: Path):
    """Page sizes near the cap must still yield an under-limit PNG after re-render."""
    path = tmp_path / "near-cap.pdf"
    # Choose dimensions where width*height*zoom^2 is just under the estimate,
    # but integer rasterization can push actual pixels over the cap.
    side = int((MAX_RENDER_PIXELS / (1.5 * 1.5)) ** 0.5)
    doc = pymupdf.open()
    page = doc.new_page(width=side, height=side)
    page.insert_text((40, 40), "near-cap", fontsize=18)
    doc.save(path)
    doc.close()

    png = get_page_png(path, 1, zoom=1.5)
    rendered = pymupdf.Pixmap(png)
    try:
        assert rendered.width * rendered.height <= MAX_RENDER_PIXELS
    finally:
        rendered = None
