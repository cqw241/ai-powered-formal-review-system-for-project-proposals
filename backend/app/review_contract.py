"""Shared W3 contract for rule executors and evidence payloads."""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session


class ReviewCheckStatus(str, Enum):
    """Stable check statuses shared by every rule executor."""

    PASS = "PASS"
    FAIL = "FAIL"
    NEED_HUMAN_REVIEW = "NEED_HUMAN_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class ReviewEvidenceBBox(BaseModel):
    """Page-space bounding box for evidence that can be highlighted later."""

    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


class ReviewEvidence(BaseModel):
    """One normalized reference back to a source material field."""

    model_config = ConfigDict(extra="forbid")

    material_id: str | None = None
    category: str | None = None
    original_filename: str | None = None
    field_name: str | None = None
    raw_value: str | None = None
    normalized_value: str | int | float | bool | None = None
    page_number: int | None = Field(default=None, ge=1)
    quote: str | None = None
    bbox: ReviewEvidenceBBox | None = None
    reliable: bool | None = None
    reason: str | None = None


class FieldOverride(BaseModel):
    """One human-corrected extracted field used when re-running executors."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    field_name: str
    raw_value: str


class RuleExecutionContext(BaseModel):
    """Inputs that the integration layer passes to a domain rule executor."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    review_item_id: str
    rule_code: str
    source_rule_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    snapshot: dict[str, Any] | None = None
    field_overrides: list[FieldOverride] = Field(default_factory=list)


class RuleExecutionResult(BaseModel):
    """Only result shape accepted from W3/W4 domain executors."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewCheckStatus
    summary: str = Field(min_length=1)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class RuleExecutor(Protocol):
    """Callable contract implemented by B07/B08/B09 domain modules."""

    def __call__(self, db: Session, context: RuleExecutionContext) -> RuleExecutionResult: ...
