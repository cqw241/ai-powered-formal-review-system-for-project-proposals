#!/usr/bin/env python3
"""Developer smoke test for the shared vision adapter.

Usage (from repo root, with backend venv active via uv):

    cd backend && uv run python scripts/smoke_vision.py

Reads ``backend/fixtures/vision_smoke.png``, calls the same ``analyze_image``
module used by future B02/B03 code, and checks that the structured result
reflects image content (not merely valid JSON).

Does not expose an unauthenticated HTTP debug endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings  # noqa: E402
from app.llm import (  # noqa: E402
    ImageInput,
    LlmConfigurationError,
    LlmError,
    analyze_image,
)

FIXTURE = BACKEND_ROOT / "fixtures" / "vision_smoke.png"

# Prompt does NOT reveal the expected glyph text or colors.
USER_PROMPT = (
    "观察这张示意图。列出你能读到的所有可见文字，描述主体颜色，"
    "并统计可辨认的色块/形状对象数量。用中文填写 summary。"
)


def _looks_like_fixture_read(result_summary: str, texts: list[str], primary_color: str) -> list[str]:
    """Return list of soft evidence that the model actually looked at the image."""
    evidence: list[str] = []
    joined = " ".join([result_summary, *texts, primary_color]).lower()
    # Fixture contains block glyphs "A42", a red rectangle, and a blue diamond.
    if any(token in joined for token in ("a42", "a 42", "a-42")):
        evidence.append("visible_text_contains_A42")
    if any(token in joined for token in ("red", "红", "赤")):
        evidence.append("mentions_red")
    if any(token in joined for token in ("blue", "蓝", "蓝")):
        evidence.append("mentions_blue")
    if result_summary.strip():
        evidence.append("non_empty_summary")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规证AI vision smoke test")
    parser.add_argument(
        "--image",
        type=Path,
        default=FIXTURE,
        help="Path to a local test image (default: fixtures/vision_smoke.png)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.llm_configured:
        print("FAIL: LLM 未配置。请在仓库根目录 `.env` 填写 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY。")
        print(f"参考模板: {REPO_ROOT / '.env.example'}")
        return 2

    image_path: Path = args.image
    if not image_path.is_file():
        print(f"FAIL: 测试图像不存在: {image_path}")
        return 2

    media_type = "image/png"
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    elif suffix == ".webp":
        media_type = "image/webp"
    elif suffix == ".gif":
        media_type = "image/gif"

    image = ImageInput(media_type=media_type, data=image_path.read_bytes())  # type: ignore[arg-type]

    print(f"model={settings.llm_model}")
    print(f"base_url={settings.llm_base_url}")
    print(f"image={image_path} bytes={len(image.data)}")

    started = time.perf_counter()
    try:
        result = analyze_image(image, prompt=USER_PROMPT, settings=settings)
    except LlmConfigurationError as exc:
        print(f"FAIL: {exc} (code={exc.code})")
        return 2
    except LlmError as exc:
        rid = f" request_id={exc.request_id}" if exc.request_id else ""
        print(f"FAIL: {exc} (code={exc.code}{rid})")
        return 1
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    evidence = _looks_like_fixture_read(result.summary, result.visible_texts, result.primary_color)
    payload = {
        "model": settings.llm_model,
        "elapsed_ms": elapsed_ms,
        "result": result.model_dump(),
        "image_read_evidence": evidence,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    # Require at least one content-based signal beyond a non-empty summary.
    strong = [item for item in evidence if item != "non_empty_summary"]
    if not strong:
        print(
            "FAIL: 结构化结果合法，但未能证明读取了测试图像内容"
            "（未识别到 A42 / 红 / 蓝 等预期信号）。"
        )
        return 1

    print(f"OK: vision smoke passed in {elapsed_ms}ms; evidence={strong}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
