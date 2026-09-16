"""Persistence helpers for generic per-rule execution results.

The project currently relies on ``Base.metadata.create_all`` instead of schema
migrations. W3 therefore stores extensible rule payloads in a new table rather
than adding columns to the already-existing ``review_items`` table.
"""

from __future__ import annotations

import json

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text, insert, select, update
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ReviewItem, ReviewItemStatus, utc_now
from app.review_contract import ReviewCheckStatus, RuleExecutionResult


review_item_results = Table(
    "review_item_results",
    Base.metadata,
    Column(
        "review_item_id",
        String(36),
        ForeignKey("review_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def item_status_for_check(check_status: ReviewCheckStatus) -> ReviewItemStatus:
    """Map a rule verdict to the existing B06 workspace item state."""
    if check_status in {ReviewCheckStatus.PASS, ReviewCheckStatus.FAIL}:
        return ReviewItemStatus.COMPLETED
    if check_status == ReviewCheckStatus.NEED_HUMAN_REVIEW:
        return ReviewItemStatus.PENDING_CONFIRMATION
    if check_status == ReviewCheckStatus.SYSTEM_ERROR:
        return ReviewItemStatus.FAILED
    raise ValueError(f"Unsupported review check status: {check_status}")


def persist_rule_result(
    db: Session,
    item: ReviewItem,
    result: RuleExecutionResult,
    *,
    commit: bool = False,
) -> None:
    """Upsert the generic payload and synchronize B06 summary/status fields."""
    check_status = result.status
    item_status = item_status_for_check(check_status)
    payload_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    now = utc_now()

    exists = db.execute(
        select(review_item_results.c.review_item_id).where(
            review_item_results.c.review_item_id == item.id
        )
    ).scalar_one_or_none()
    if exists is None:
        db.execute(
            insert(review_item_results).values(
                review_item_id=item.id,
                payload_json=payload_json,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        db.execute(
            update(review_item_results)
            .where(review_item_results.c.review_item_id == item.id)
            .values(payload_json=payload_json, updated_at=now)
        )

    item.status = item_status.value
    item.check_status = check_status.value
    item.summary = result.summary
    db.add(item)
    if commit:
        db.commit()


def load_rule_result(db: Session, review_item_id: str) -> RuleExecutionResult | None:
    """Load and validate one persisted generic result payload."""
    payload_json = db.execute(
        select(review_item_results.c.payload_json).where(
            review_item_results.c.review_item_id == review_item_id
        )
    ).scalar_one_or_none()
    if payload_json is None:
        return None
    return RuleExecutionResult.model_validate(json.loads(payload_json))
