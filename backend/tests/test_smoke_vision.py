"""Unit tests for vision smoke pass criteria (no live API)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.llm.schemas import ImageAnalysisResult

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_vision.py"
_SPEC = importlib.util.spec_from_file_location("smoke_vision", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
smoke_vision = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke_vision)


def _result(**overrides: object) -> ImageAnalysisResult:
    payload = {
        "summary": "红色与蓝色色块",
        "visible_texts": ["A42"],
        "primary_color": "mixed",
        "object_count": 2,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return ImageAnalysisResult.model_validate(payload)


def test_smoke_requires_both_red_and_blue():
    red_only = smoke_vision.collect_fixture_evidence("左侧红色方块", [], "red")
    assert "mentions_red" in red_only
    assert "mentions_blue" not in red_only
    ok, reason = smoke_vision.smoke_result_passes(_result(object_count=2), red_only)
    assert ok is False
    assert "红" in reason and "蓝" in reason

    blue_only = smoke_vision.collect_fixture_evidence("右侧蓝色菱形", [], "blue")
    assert "mentions_blue" in blue_only
    assert "mentions_red" not in blue_only
    ok, reason = smoke_vision.smoke_result_passes(_result(object_count=2), blue_only)
    assert ok is False
    assert "红" in reason and "蓝" in reason


def test_smoke_requires_object_count_at_least_two():
    evidence = smoke_vision.collect_fixture_evidence(
        "红色正方形和蓝色菱形",
        [],
        "红蓝",
    )
    ok, reason = smoke_vision.smoke_result_passes(_result(object_count=1), evidence)
    assert ok is False
    assert "object_count" in reason


def test_smoke_passes_with_both_colors_without_exact_ocr():
    evidence = smoke_vision.collect_fixture_evidence(
        "画面有红色方块与蓝色菱形",
        ["unknown"],
        "多色",
    )
    assert "visible_text_contains_A42" not in evidence
    ok, _reason = smoke_vision.smoke_result_passes(_result(object_count=2), evidence)
    assert ok is True
