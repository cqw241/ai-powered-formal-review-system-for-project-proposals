"""RULE-002/003: cross-file project name and principal consistency.

Executor contract: Session + RuleExecutionContext → RuleExecutionResult.
Comparison is programmatic. Extraction never treats a missing/unreadable
field as a confirmed conflict.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Material, MaterialCategory, MaterialStatus
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    ReviewEvidenceBBox,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.services.identity_extract import (
    BBox,
    ExtractedIdentity,
    extract_principal_from_pdf,
    extract_project_name_from_pdf,
)
from app.services.storage import material_file_path

RULE_002 = "RULE-002"
RULE_003 = "RULE-003"

_REQUIRED_CATEGORIES = (
    MaterialCategory.APPLICATION,
    MaterialCategory.BUDGET,
    MaterialCategory.COMMITMENT,
)

_CATEGORY_LABEL = {
    MaterialCategory.APPLICATION.value: "申报书",
    MaterialCategory.BUDGET.value: "预算表",
    MaterialCategory.COMMITMENT.value: "承诺书",
}

_ExtractFn = Callable[[Path], ExtractedIdentity]


def execute_identity_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """Run RULE-002 or RULE-003 against the project's latest ready materials."""
    if context.rule_code == RULE_002:
        return _execute_field(
            db,
            context,
            field_name="项目名称",
            extract=extract_project_name_from_pdf,
        )
    if context.rule_code == RULE_003:
        return _execute_field(
            db,
            context,
            field_name="项目负责人",
            extract=extract_principal_from_pdf,
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus.SYSTEM_ERROR,
        summary=f"identity 执行器不支持规则 {context.rule_code}",
        data={"rule_code": context.rule_code},
    )


def _execute_field(
    db: Session,
    context: RuleExecutionContext,
    *,
    field_name: str,
    extract: _ExtractFn,
) -> RuleExecutionResult:
    try:
        return _compare_field(db, context, field_name=field_name, extract=extract)
    except Exception as exc:  # noqa: BLE001 — surface as SYSTEM_ERROR
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"{field_name}核对过程失败：{exc}",
            data={"rule_code": context.rule_code, "check_field": field_name},
        )


def _compare_field(
    db: Session,
    context: RuleExecutionContext,
    *,
    field_name: str,
    extract: _ExtractFn,
) -> RuleExecutionResult:
    cfg = get_settings()
    sides: list[tuple[Material | None, str, ExtractedIdentity]] = []
    missing: list[str] = []

    for category in _REQUIRED_CATEGORIES:
        material = _latest_ready_material(db, context.project_id, category)
        if material is None:
            missing.append(_CATEGORY_LABEL[category.value])
            sides.append(
                (
                    None,
                    category.value,
                    ExtractedIdentity(
                        field_kind="project_name" if field_name == "项目名称" else "principal",
                        field_label=field_name,
                        raw_value=None,
                        normalized_value=None,
                        page_number=None,
                        quote=None,
                        bbox=None,
                        reliable=False,
                        reason=f"缺少就绪的{_CATEGORY_LABEL[category.value]}，不能把尚未找到当成确认冲突",
                        source="none",
                    ),
                )
            )
            continue
        extracted = extract(material_file_path(material.id, cfg))
        sides.append((material, category.value, extracted))

    evidence = [_to_evidence(material, category, extracted) for material, category, extracted in sides]
    reliable = [
        (category, extracted)
        for _material, category, extracted in sides
        if extracted.reliable and extracted.normalized_value
    ]
    unique_values = {extracted.normalized_value for _category, extracted in reliable}
    data = {
        "rule_code": context.rule_code,
        "check_field": field_name,
        "normalized_values": sorted(unique_values),
        "missing_categories": missing,
    }

    if len(unique_values) >= 2:
        summary = _conflict_summary(field_name, reliable)
        return RuleExecutionResult(
            status=ReviewCheckStatus.FAIL,
            summary=summary,
            evidence=evidence,
            data=data,
        )

    unresolved = [
        extracted
        for _material, _category, extracted in sides
        if not extracted.reliable or not extracted.normalized_value
    ]
    if unresolved:
        reasons = [item.reason for item in unresolved if item.reason]
        summary = reasons[0] if reasons else f"{field_name}无法可靠读取，需人工确认"
        return RuleExecutionResult(
            status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
            summary=summary,
            evidence=evidence,
            data=data,
        )

    agreed = next(iter(unique_values))
    return RuleExecutionResult(
        status=ReviewCheckStatus.PASS,
        summary=f"三类材料{field_name}规范化后一致：{agreed}",
        evidence=evidence,
        data=data,
    )


def _conflict_summary(
    field_name: str,
    reliable: list[tuple[str, ExtractedIdentity]],
) -> str:
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for category, extracted in reliable:
        key = (category, extracted.normalized_value or "")
        if key in seen:
            continue
        seen.add(key)
        label = _CATEGORY_LABEL.get(category, category)
        parts.append(f"{label}「{extracted.normalized_value}」")
    return f"{field_name}不一致：" + "，".join(parts)


def _to_evidence(
    material: Material | None,
    category: str,
    extracted: ExtractedIdentity,
) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=material.id if material is not None else None,
        category=category,
        original_filename=material.original_filename if material is not None else None,
        field_name=extracted.field_label,
        raw_value=extracted.raw_value,
        normalized_value=extracted.normalized_value,
        page_number=extracted.page_number,
        quote=extracted.quote,
        bbox=_to_evidence_bbox(extracted.bbox),
        reliable=extracted.reliable,
        reason=extracted.reason,
    )


def _to_evidence_bbox(bbox: BBox | None) -> ReviewEvidenceBBox | None:
    if bbox is None:
        return None
    return ReviewEvidenceBBox(
        x0=bbox.x0,
        y0=bbox.y0,
        x1=bbox.x1,
        y1=bbox.y1,
        page_width=bbox.page_width,
        page_height=bbox.page_height,
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
