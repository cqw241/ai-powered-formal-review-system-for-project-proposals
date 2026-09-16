"""ORM models for projects, PDF materials, funding reviews, policies, rules, and review tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MaterialCategory(str, Enum):
    APPLICATION = "APPLICATION"
    BUDGET = "BUDGET"
    COMMITMENT = "COMMITMENT"
    OTHER = "OTHER"


class MaterialStatus(str, Enum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class FundingReviewStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEED_HUMAN_REVIEW = "NEED_HUMAN_REVIEW"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class ReviewTaskStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class ReviewItemStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"


class PolicyStatus(str, Enum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    materials: Mapped[list[Material]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    funding_reviews: Mapped[list[FundingReview]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    review_tasks: Mapped[list[ReviewTask]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MaterialStatus.PROCESSING.value)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    project: Mapped[Project] = relationship(back_populates="materials")


class FundingReview(Base):
    """Persisted RULE-007 application-funding comparison for a project."""

    __tablename__ = "funding_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(32), nullable=False, default="RULE-007")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    project: Mapped[Project] = relationship(back_populates="funding_reviews")


class PolicyDocument(Base):
    """Uploaded policy PDF (e.g. batch declaration guide)."""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PolicyStatus.PROCESSING.value)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    candidates: Mapped[list[PolicyCandidate]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="PolicyCandidate.sort_order",
    )


class PolicyCandidate(Base):
    """Editable draft requirement extracted from a policy. Not an enabled rule."""

    __tablename__ = "policy_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_yuan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comparator: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_clause: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    policy: Mapped[PolicyDocument] = relationship(back_populates="candidates")
    rule: Mapped[Rule | None] = relationship(
        back_populates="source_candidate",
        uselist=False,
    )


class Rule(Base):
    """Enabled review rule. Disable flips enabled; versions are kept."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_candidate_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("policy_candidates.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    source_candidate: Mapped[PolicyCandidate | None] = relationship(back_populates="rule")
    versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="RuleVersion.version_number",
    )


class RuleVersion(Base):
    """Immutable snapshot of a rule's requirement. Edits append a new row."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version_number", name="uq_rule_version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    compare_field: Mapped[str] = mapped_column(String(64), nullable=False, default="申请经费")
    comparator: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount_yuan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_clause: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    rule: Mapped[Rule] = relationship(back_populates="versions")


class ReviewTask(Base):
    """Persisted review run: RULE-007 plus user-selected enabled rules."""

    __tablename__ = "review_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ReviewTaskStatus.RUNNING.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    project: Mapped[Project] = relationship(back_populates="review_tasks")
    items: Mapped[list[ReviewItem]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ReviewItem.sort_order",
    )


class ReviewItem(Base):
    """One rule slot in a review task. RULE-007 is built-in, not a rules-table row."""

    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    source_rule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    check_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    funding_review_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("funding_reviews.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    task: Mapped[ReviewTask] = relationship(back_populates="items")
