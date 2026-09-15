"""Application-owned structures for vision calls (no vendor SDK types)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImageInput(BaseModel):
    """One image payload for the multimodal adapter."""

    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data: bytes = Field(..., min_length=1)

    @field_validator("data")
    @classmethod
    def _non_empty(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("image data must not be empty")
        return value


class ImageAnalysisResult(BaseModel):
    """Structured vision result used by the application and smoke tests.

    Fields are intentionally generic so B02/B03 can reuse the same adapter
    while supplying their own prompts and (later) tighter schemas.

    Strict mode rejects stringly-typed numbers (e.g. ``"2"``, ``"0.5"``) and
    unknown fields; callers must not treat coercion as validation success.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    summary: str = Field(..., min_length=1, description="简短中文摘要")
    visible_texts: list[str] = Field(
        default_factory=list,
        description="图中可见文字（按阅读顺序，可为空）",
    )
    primary_color: str = Field(
        ...,
        min_length=1,
        description="画面主体颜色的简短描述，例如 red / blue / green",
    )
    object_count: int = Field(..., ge=0, description="可辨认主体对象数量")
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_from_json_number(cls, value: object) -> object:
        # JSON has one number type; json.loads may yield int for whole values.
        # Allow int→float only. Reject strings like "0.5" and bools.
        if isinstance(value, bool) or isinstance(value, str):
            raise ValueError("confidence must be a JSON number, not a string or boolean")
        if isinstance(value, int):
            return float(value)
        return value



class AnalyzeImageOutcome(BaseModel):
    """Application-facing vision call outcome, including transport metadata."""

    result: ImageAnalysisResult
    request_id: str | None = None


def image_analysis_json_schema() -> dict[str, Any]:
    """JSON Schema sent to providers that support response_format.json_schema."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "visible_texts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "primary_color": {"type": "string", "minLength": 1},
            "object_count": {"type": "integer", "minimum": 0},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "summary",
            "visible_texts",
            "primary_color",
            "object_count",
            "confidence",
        ],
    }
