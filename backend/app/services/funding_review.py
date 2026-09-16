"""RULE-007: compare application funding across APPLICATION and BUDGET materials.

Program owns comparison arithmetic. Extraction may use text or vision, but
status / difference / normalization are computed here.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    FundingReview,
    FundingReviewStatus,
    Material,
    MaterialCategory,
    MaterialStatus,
)
from app.schemas import FundingFinding, FundingReviewRead
from app.services.funding_extract import (
    ExtractedFunding,
    extract_application_funding_from_pdf,
)
from app.services.money import UNIT_LABELS, difference_yuan
from app.services.storage import material_file_path

RULE_ID = "RULE-007"
CHECK_FIELD = "申请经费"


@dataclass(frozen=True, slots=True)
class SideView:
    material_id: str
    category: str
    original_filename: str
    extraction: ExtractedFunding

    def as_dict(self) -> dict[str, Any]:
        data = self.extraction.as_dict()
        data.update(
            {
                "material_id": self.material_id,
                "category": self.category,
                "original_filename": self.original_filename,
                "normalized_amount_yuan": self.extraction.amount_yuan,
                "normalized_unit": "元" if self.extraction.amount_yuan is not None else None,
                "display_unit": (
                    self.extraction.raw_unit
                    or (
                        UNIT_LABELS["yuan"]
                        if self.extraction.amount_yuan is not None
                        else None
                    )
                ),
            }
        )
        return data


def run_funding_review(
    db: Session,
    project_id: str,
    *,
    settings: Settings | None = None,
    use_llm_fallback: bool | None = None,
) -> FundingReview:
    """Extract, compare, persist, and return the latest funding review for a project."""
    cfg = settings or get_settings()
    llm_fallback = cfg.llm_configured if use_llm_fallback is None else use_llm_fallback

    application = _latest_ready_material(db, project_id, MaterialCategory.APPLICATION)
    budget = _latest_ready_material(db, project_id, MaterialCategory.BUDGET)

    if application is None or budget is None:
        missing = []
        if application is None:
            missing.append("申报书 (APPLICATION)")
        if budget is None:
            missing.append("预算表 (BUDGET)")
        payload = _human_payload(
            reason=f"缺少就绪材料：{'、'.join(missing)}。上传并解析成功后再核对。",
            left=None,
            right=None,
            difference=None,
        )
        return _persist(db, project_id, FundingReviewStatus.NEED_HUMAN_REVIEW, payload)

    try:
        app_path = material_file_path(application.id, cfg)
        budget_path = material_file_path(budget.id, cfg)
        left_ext = extract_application_funding_from_pdf(
            app_path,
            prefer_labels=("申请经费", "申请金额", "申请资助经费"),
            use_llm_fallback=llm_fallback,
        )
        right_ext = extract_application_funding_from_pdf(
            budget_path,
            prefer_labels=("申请总额", "申请经费", "申请金额"),
            use_llm_fallback=llm_fallback,
        )
    except Exception as exc:  # noqa: BLE001 — surface as SYSTEM_ERROR finding
        payload = _error_payload(f"经费核对过程失败：{exc}")
        return _persist(db, project_id, FundingReviewStatus.SYSTEM_ERROR, payload)

    left = SideView(
        material_id=application.id,
        category=application.category,
        original_filename=application.original_filename,
        extraction=left_ext,
    )
    right = SideView(
        material_id=budget.id,
        category=budget.category,
        original_filename=budget.original_filename,
        extraction=right_ext,
    )

    status, reason, diff = _compare(left_ext, right_ext)
    payload = {
        "rule_id": RULE_ID,
        "check_field": CHECK_FIELD,
        "status": status.value,
        "reason": reason,
        "difference_yuan": diff,
        "left": left.as_dict(),
        "right": right.as_dict(),
        "compared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return _persist(db, project_id, status, payload)


def get_latest_funding_review(db: Session, project_id: str) -> FundingReview | None:
    statement = (
        select(FundingReview)
        .where(FundingReview.project_id == project_id)
        .order_by(FundingReview.created_at.desc())
        .limit(1)
    )
    return db.scalars(statement).first()


def to_funding_review_read(review: FundingReview) -> FundingReviewRead:
    """Build API response the same way Project/Material use response models."""
    finding = FundingFinding.model_validate(json.loads(review.payload_json))
    return FundingReviewRead(
        id=review.id,
        project_id=review.project_id,
        rule_id=review.rule_id,
        status=FundingReviewStatus(review.status),
        created_at=review.created_at,
        finding=finding,
    )


def _compare(
    left: ExtractedFunding,
    right: ExtractedFunding,
) -> tuple[FundingReviewStatus, str, int | None]:
    if not left.reliable or left.amount_yuan is None:
        return (
            FundingReviewStatus.NEED_HUMAN_REVIEW,
            left.reason or "申报书申请经费无法可靠读取，需人工确认",
            None,
        )
    if not right.reliable or right.amount_yuan is None:
        return (
            FundingReviewStatus.NEED_HUMAN_REVIEW,
            right.reason or "预算表申请经费无法可靠读取，需人工确认",
            None,
        )

    if left.field_kind != "application_funding" or right.field_kind != "application_funding":
        return (
            FundingReviewStatus.NEED_HUMAN_REVIEW,
            "字段含义不清：未能确认两侧均为申请经费（可能与项目总经费混淆）",
            None,
        )

    diff = difference_yuan(left.amount_yuan, right.amount_yuan)
    assert diff is not None
    if diff == 0:
        return (
            FundingReviewStatus.PASS,
            (
                f"两侧申请经费规范化后均为 {left.amount_yuan} 元"
                f"（申报书原文「{left.raw_value}」，预算表原文「{right.raw_value}」）"
            ),
            0,
        )
    return (
        FundingReviewStatus.FAIL,
        (
            f"申报书申请经费规范化为 {left.amount_yuan} 元，"
            f"预算表申请经费规范化为 {right.amount_yuan} 元，差额 {diff} 元"
        ),
        diff,
    )


def _latest_ready_material(
    db: Session,
    project_id: str,
    category: MaterialCategory,
) -> Material | None:
    statement = (
        select(Material)
        .where(
            Material.project_id == project_id,
            Material.category == category.value,
            Material.status == MaterialStatus.READY.value,
        )
        .order_by(Material.created_at.desc())
        .limit(1)
    )
    return db.scalars(statement).first()


def _persist(
    db: Session,
    project_id: str,
    status: FundingReviewStatus,
    payload: dict[str, Any],
) -> FundingReview:
    review = FundingReview(
        id=str(uuid.uuid4()),
        project_id=project_id,
        rule_id=RULE_ID,
        status=status.value,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def _human_payload(
    *,
    reason: str,
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    difference: int | None,
) -> dict[str, Any]:
    return {
        "rule_id": RULE_ID,
        "check_field": CHECK_FIELD,
        "status": FundingReviewStatus.NEED_HUMAN_REVIEW.value,
        "reason": reason,
        "difference_yuan": difference,
        "left": left,
        "right": right,
        "compared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _error_payload(reason: str) -> dict[str, Any]:
    return {
        "rule_id": RULE_ID,
        "check_field": CHECK_FIELD,
        "status": FundingReviewStatus.SYSTEM_ERROR.value,
        "reason": reason,
        "difference_yuan": None,
        "left": None,
        "right": None,
        "compared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
