"""API request/response schemas for project and material resources."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.models import (
    FundingReviewStatus,
    MaterialCategory,
    MaterialStatus,
    PolicyStatus,
    ReviewItemStatus,
    ReviewTaskStatus,
)
from app.review_contract import RuleExecutionResult


class ProjectCreate(BaseModel):
    name: str = Field(..., description="项目名称")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("项目名称不能为空")
        if len(cleaned) > 200:
            raise ValueError("项目名称不能超过 200 个字符")
        return cleaned


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        # SQLite may round-trip timezone-aware values as naive; treat naive as UTC.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    original_filename: str
    category: MaterialCategory
    page_count: int | None
    status: MaterialStatus
    error_summary: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PageTextResponse(BaseModel):
    material_id: str
    page_number: int
    page_count: int
    text: str


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool


class EvidenceBBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


class FundingSide(BaseModel):
    material_id: str | None = None
    category: MaterialCategory | None = None
    original_filename: str | None = None
    field_kind: str | None = None
    field_label: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    amount_yuan: int | None = None
    normalized_amount_yuan: int | None = None
    normalized_unit: str | None = None
    display_unit: str | None = None
    page_number: int | None = None
    quote: str | None = None
    bbox: EvidenceBBox | None = None
    reliable: bool | None = None
    reason: str | None = None
    source: str | None = None


class FundingFinding(BaseModel):
    rule_id: str
    check_field: str
    status: FundingReviewStatus
    reason: str
    difference_yuan: int | None = None
    left: FundingSide | None = None
    right: FundingSide | None = None
    compared_at: datetime | None = None

    @field_validator("compared_at")
    @classmethod
    def ensure_compared_at_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("compared_at")
    def serialize_compared_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class BoundRuleSnapshot(BaseModel):
    """Frozen enabled-rule version bound to one funding-review run."""

    rule_id: str
    version_id: str
    version_number: int
    rule_code: str
    name: str
    category: str | None = None
    compare_field: str
    comparator: str | None = None
    amount_yuan: int | None = None
    amount_raw: str | None = None
    source_clause: str | None = None
    source_page: int | None = None
    source_quote: str | None = None
    policy_id: str | None = None
    application_amount_yuan: int | None = None
    display_note: str | None = None


class FundingReviewRead(BaseModel):
    """Aligned with ProjectRead / MaterialRead: ORM-friendly + UTC ISO created_at."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    rule_id: str
    status: FundingReviewStatus
    created_at: datetime
    finding: FundingFinding
    bound_rules: list[BoundRuleSnapshot] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RuleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_id: str
    version_number: int
    name: str
    category: str | None
    compare_field: str
    comparator: str | None
    amount_yuan: int | None
    amount_raw: str | None
    source_clause: str | None
    source_page: int | None
    source_quote: str | None
    policy_id: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _serialize_utc(value)


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_code: str
    name: str
    enabled: bool
    source_candidate_id: str | None
    policy_id: str | None
    current_version_number: int
    created_at: datetime
    versions: list[RuleVersionRead] = Field(default_factory=list)
    current_version: RuleVersionRead | None = None

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _serialize_utc(value)

    @model_validator(mode="after")
    def attach_current_version(self) -> RuleRead:
        if self.current_version is None and self.versions:
            match = next(
                (item for item in self.versions if item.version_number == self.current_version_number),
                None,
            )
            self.current_version = match or self.versions[-1]
        return self


class RuleUpdate(BaseModel):
    """Partial update for an enabled rule; applied as a new immutable version."""

    name: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=100)
    comparator: str | None = Field(default=None, max_length=16)
    amount_raw: str | None = Field(default=None, max_length=64)
    source_clause: str | None = Field(default=None, max_length=64)
    source_page: int | None = Field(default=None, ge=1)
    source_quote: str | None = None

    @field_validator("name", "category", "comparator", "amount_raw", "source_clause", "source_quote")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class PolicyCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    policy_id: str
    kind: str
    title: str
    category: str | None
    amount_raw: str | None
    amount_yuan: int | None
    amount_unit: str | None
    comparator: str | None
    source_clause: str | None
    source_page: int | None
    source_quote: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    rule: RuleRead | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> str:
        return _serialize_utc(value)


class PolicyCandidateUpdate(BaseModel):
    """Partial update for an editable draft candidate."""

    title: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=100)
    amount_raw: str | None = Field(default=None, max_length=64)
    comparator: str | None = Field(default=None, max_length=16)
    source_clause: str | None = Field(default=None, max_length=64)
    source_page: int | None = Field(default=None, ge=1)
    source_quote: str | None = None

    @field_validator("title", "category", "amount_raw", "comparator", "source_clause", "source_quote")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class PolicySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    title: str | None
    page_count: int | None
    status: PolicyStatus
    error_summary: str | None
    created_at: datetime
    candidate_count: int = 0

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _serialize_utc(value)


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    title: str | None
    page_count: int | None
    status: PolicyStatus
    error_summary: str | None
    created_at: datetime
    candidates: list[PolicyCandidateRead] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _serialize_utc(value)


class PolicyPageTextResponse(BaseModel):
    policy_id: str
    page_number: int
    page_count: int
    text: str


class ReviewTaskCreate(BaseModel):
    """Selected enabled rules from GET /api/rules. Built-in W3/W4 rules are always included."""

    rule_ids: list[str] = Field(default_factory=list)


class CompareSide(BaseModel):
    """One column in the B11 dual-document compare view."""

    material_id: str | None = None
    category: str | None = None
    original_filename: str | None = None
    field_name: str
    field_kind: str | None = None
    raw_value: str | None = None
    unit: str | None = None
    normalized_value: str | int | float | bool | None = None
    page_number: int | None = None
    quote: str | None = None
    bbox: EvidenceBBox | None = None
    reliable: bool | None = None
    reason: str | None = None
    openable: bool = False


class LabeledFundingField(BaseModel):
    """A labeled amount mention; 申请经费 and 总经费 stay distinct."""

    field_name: str
    field_kind: str
    raw_value: str | None = None
    unit: str | None = None
    page_number: int | None = None
    bbox: EvidenceBBox | None = None
    material_id: str | None = None
    original_filename: str | None = None


class EvidenceCompareView(BaseModel):
    """Generic issue detail for name / amount / date evidence."""

    check_field: str
    difference: str | None = None
    difference_yuan: int | None = None
    sides: list[CompareSide] = Field(default_factory=list)
    funding_fields: list[LabeledFundingField] = Field(default_factory=list)


class ReviewItemRead(BaseModel):
    id: str
    rule_code: str
    source_rule_id: str | None
    name: str
    status: ReviewItemStatus
    check_status: FundingReviewStatus | None = None
    summary: str
    version_id: str | None = None
    version_number: int | None = None
    snapshot: BoundRuleSnapshot | None = None
    funding_review_id: str | None = None
    sort_order: int
    result: RuleExecutionResult | None = None
    compare: EvidenceCompareView | None = None


class ReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    status: ReviewTaskStatus
    created_at: datetime
    updated_at: datetime
    items: list[ReviewItemRead] = Field(default_factory=list)

    @field_validator("created_at", "updated_at")
    @classmethod
    def ensure_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> str:
        return _serialize_utc(value)
