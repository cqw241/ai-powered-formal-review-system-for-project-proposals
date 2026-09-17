"""RULE-005 / RULE-006 executors.

Integration calls ``execute_budget_rule(db, context)``. This module does not
write review_items or touch the workspace; it only returns RuleExecutionResult.
RULE-005 uses the bound version snapshot for category cap; amounts reuse
funding_extract / money. Missing or unclear values → NEED_HUMAN_REVIEW, never FAIL.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Material, MaterialCategory, MaterialStatus
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    ReviewEvidenceBBox,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.services.budget_extract import (
    BudgetLine,
    ExtractedBudget,
    ExtractedCategory,
    extract_budget,
    extract_project_category,
)
from app.services.field_overrides import apply_funding_override
from app.services.funding_extract import BBox, ExtractedFunding, extract_application_funding_from_pdf
from app.services.money import difference_yuan
from app.services.storage import material_file_path

RULE_005 = "RULE-005"
RULE_006 = "RULE-006"
SUM_TOLERANCE_YUAN = 1
APPLICATION_LABELS = ("申请经费", "申请金额", "申请资助经费")
BUDGET_TOTAL_LABELS = ("申请总额", "申请经费", "申请金额")


def execute_budget_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """W3 RuleExecutor entry for B08."""
    try:
        if context.rule_code == RULE_005:
            return execute_funding_cap_rule(db, context)
        if context.rule_code == RULE_006:
            return execute_budget_sum_rule(db, context)
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"预算规则执行器不支持 {context.rule_code}",
            data={"rule_code": context.rule_code},
        )
    except Exception as exc:  # noqa: BLE001 — surface as SYSTEM_ERROR, never as material FAIL
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"预算规则执行失败：{exc}",
            data={"rule_code": context.rule_code, "error": str(exc)},
        )


def execute_funding_cap_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """RULE-005: 分类申请经费上限. Cap comes from the bound RULE-005 snapshot."""
    snapshot = context.snapshot or {}
    application = _latest_ready_material(db, context.project_id, MaterialCategory.APPLICATION)
    budget = _latest_ready_material(db, context.project_id, MaterialCategory.BUDGET)
    settings = get_settings()

    evidence: list[ReviewEvidence] = []
    data: dict[str, Any] = {
        "rule_code": RULE_005,
        "snapshot_category": snapshot.get("category"),
        "snapshot_amount_yuan": snapshot.get("amount_yuan"),
        "snapshot_comparator": snapshot.get("comparator") or "LE",
        "version_id": context.version_id,
        "version_number": context.version_number,
    }

    if application is None:
        return _human(
            "缺少就绪的申报书，无法读取项目类别，不能选择经费上限",
            evidence=evidence,
            data=data,
            fields=["项目类别"],
        )

    category = extract_project_category(_material_path(application, settings))
    evidence.append(
        _category_evidence(application, category),
    )
    data["project_category"] = category.normalized
    data["project_category_raw"] = category.raw_value

    if not category.reliable or category.normalized is None:
        return _human(
            category.reason or "项目类别无法确定，无法选择经费上限；待确认字段：项目类别",
            evidence=evidence,
            data=data,
            fields=["项目类别"],
        )

    cap_category = (snapshot.get("category") or "").strip() or None
    cap_yuan = snapshot.get("amount_yuan")
    comparator = str(snapshot.get("comparator") or "LE").upper()
    data["cap_category"] = cap_category
    data["cap_yuan"] = cap_yuan
    data["comparator"] = comparator

    if cap_yuan is None:
        return _human(
            "绑定规则未给出经费上限金额，未知值不能当作 0；待确认字段：经费上限",
            evidence=evidence,
            data=data,
            fields=["经费上限"],
        )
    try:
        cap_yuan_int = int(cap_yuan)
    except (TypeError, ValueError):
        return _human(
            "绑定规则的经费上限无法解析，未知值不能当作 0；待确认字段：经费上限",
            evidence=evidence,
            data=data,
            fields=["经费上限"],
        )

    if cap_category and cap_category != category.normalized:
        return _human(
            (
                f"项目类别为{category.normalized}，与绑定规则类别{cap_category}不一致，"
                "无法使用该上限；待确认字段：项目类别"
            ),
            evidence=evidence,
            data=data,
            fields=["项目类别"],
        )

    amount, amount_evidence = _resolve_application_amount(
        application=application,
        budget=budget,
        snapshot=snapshot,
        settings=settings,
        overrides=context.field_overrides,
    )
    evidence.extend(amount_evidence)
    data["application_amount_yuan"] = amount.amount_yuan if amount is not None else None

    if amount is None or not amount.reliable or amount.amount_yuan is None:
        reason = (
            amount.reason
            if amount is not None
            else "申请经费无法可靠读取，不能当作 0，需人工确认"
        )
        return _human(
            f"{reason}；待确认字段：申请经费",
            evidence=evidence,
            data=data,
            fields=["申请经费"],
        )

    evidence.append(
        ReviewEvidence(
            field_name="经费上限",
            raw_value=str(snapshot.get("amount_raw") or cap_yuan_int),
            normalized_value=cap_yuan_int,
            quote=snapshot.get("source_quote"),
            page_number=snapshot.get("source_page"),
            reliable=True,
            reason=None,
        )
    )

    amount_yuan = int(amount.amount_yuan)
    within = _within_cap(amount_yuan, cap_yuan_int, comparator)
    diff = difference_yuan(amount_yuan, cap_yuan_int)
    data["difference_yuan"] = None if within else diff
    data["within_cap"] = within

    if within:
        return RuleExecutionResult(
            status=ReviewCheckStatus.PASS,
            summary=(
                f"{category.normalized}申请经费 {amount_yuan} 元，"
                f"未超过上限 {cap_yuan_int} 元"
            ),
            evidence=evidence,
            data=data,
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus.FAIL,
        summary=(
            f"{category.normalized}申请经费 {amount_yuan} 元，"
            f"超出上限 {cap_yuan_int} 元，差额 {diff} 元"
        ),
        evidence=evidence,
        data=data,
    )


def execute_budget_sum_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """RULE-006: sum(科目明细) vs 申请总额, tolerance 1 yuan."""
    budget = _latest_ready_material(db, context.project_id, MaterialCategory.BUDGET)
    data: dict[str, Any] = {
        "rule_code": RULE_006,
        "tolerance_yuan": SUM_TOLERANCE_YUAN,
    }
    evidence: list[ReviewEvidence] = []

    if budget is None:
        return _human(
            "缺少就绪的经费预算表，无法核对科目合计；待确认字段：预算科目",
            evidence=evidence,
            data=data,
            fields=["预算科目"],
        )

    extracted = extract_budget(_material_path(budget, get_settings()))
    overridden_total = apply_funding_override(
        extracted.application_total,
        budget.id,
        context.field_overrides,
        "申请总额",
    )
    if overridden_total is not extracted.application_total:
        extracted = replace(extracted, application_total=overridden_total)
    evidence.extend(_budget_evidence(budget, extracted))
    data.update(
        {
            "application_total_yuan": extracted.application_total.amount_yuan,
            "line_sum_yuan": extracted.line_sum_yuan,
            "line_names": [item.name for item in extracted.detail_lines],
            "scope_uncertain": extracted.scope_uncertain,
        }
    )

    if extracted.scope_uncertain:
        return _human(
            (extracted.scope_reason or extracted.reason or "科目计算口径不清")
            + "；待确认字段：配套经费",
            evidence=evidence,
            data=data,
            fields=["配套经费"],
        )

    if not extracted.reliable or extracted.line_sum_yuan is None:
        return _human(
            (extracted.reason or "预算科目无法可靠读取，不能当作 0")
            + "；待确认字段：预算科目",
            evidence=evidence,
            data=data,
            fields=["预算科目"],
        )

    total = extracted.application_total
    if not total.reliable or total.amount_yuan is None:
        return _human(
            (total.reason or "申请总额无法可靠读取，不能当作 0")
            + "；待确认字段：申请总额",
            evidence=evidence,
            data=data,
            fields=["申请总额"],
        )

    diff = difference_yuan(extracted.line_sum_yuan, total.amount_yuan)
    assert diff is not None
    data["difference_yuan"] = diff

    if diff <= SUM_TOLERANCE_YUAN:
        return RuleExecutionResult(
            status=ReviewCheckStatus.PASS,
            summary=(
                f"预算明细合计 {extracted.line_sum_yuan} 元，"
                f"与申请总额 {total.amount_yuan} 元一致"
                + (f"（差额 {diff} 元，未超过 {SUM_TOLERANCE_YUAN} 元容差）" if diff else "")
            ),
            evidence=evidence,
            data=data,
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus.FAIL,
        summary=(
            f"预算明细合计 {extracted.line_sum_yuan} 元，"
            f"申请总额 {total.amount_yuan} 元，合计差额 {diff} 元"
        ),
        evidence=evidence,
        data=data,
    )


def _resolve_application_amount(
    *,
    application: Material,
    budget: Material | None,
    snapshot: dict[str, Any],
    settings: Settings,
    overrides: list | None = None,
) -> tuple[ExtractedFunding | None, list[ReviewEvidence]]:
    """Prefer 预算申请总额 (CASE-014 uses 153000), then 申报书申请经费, then snapshot."""
    evidence: list[ReviewEvidence] = []
    budget_ext: ExtractedFunding | None = None
    app_ext: ExtractedFunding | None = None

    if budget is not None:
        budget_ext = extract_application_funding_from_pdf(
            _material_path(budget, settings),
            prefer_labels=BUDGET_TOTAL_LABELS,
            use_llm_fallback=False,
        )
        budget_ext = apply_funding_override(budget_ext, budget.id, overrides, "申请总额")
        evidence.append(_funding_evidence(budget, budget_ext, field_name="申请总额"))

    app_ext = extract_application_funding_from_pdf(
        _material_path(application, settings),
        prefer_labels=APPLICATION_LABELS,
        use_llm_fallback=False,
    )
    app_ext = apply_funding_override(app_ext, application.id, overrides, "申请经费")
    evidence.append(_funding_evidence(application, app_ext, field_name="申请经费"))

    if budget_ext is not None and budget_ext.reliable and budget_ext.amount_yuan is not None:
        return budget_ext, evidence
    if app_ext.reliable and app_ext.amount_yuan is not None:
        return app_ext, evidence

    snapshot_amount = snapshot.get("application_amount_yuan")
    if snapshot_amount is not None:
        try:
            yuan = int(snapshot_amount)
        except (TypeError, ValueError):
            yuan = None
        if yuan is not None:
            fallback = ExtractedFunding(
                field_kind="application_funding",
                field_label="申请经费",
                raw_value=str(yuan),
                raw_unit="元",
                amount_yuan=yuan,
                page_number=None,
                quote=None,
                bbox=None,
                reliable=True,
                reason=None,
                source="none",
            )
            evidence.append(
                ReviewEvidence(
                    field_name="申请经费",
                    raw_value=str(yuan),
                    normalized_value=yuan,
                    reliable=True,
                    reason="使用审查任务绑定快照中的申请经费",
                )
            )
            return fallback, evidence

    unresolved = budget_ext or app_ext
    return unresolved, evidence


def _within_cap(amount: int, cap: int, comparator: str) -> bool:
    if comparator == "LT":
        return amount < cap
    if comparator == "GE":
        return amount >= cap
    if comparator == "GT":
        return amount > cap
    if comparator == "EQ":
        return amount == cap
    return amount <= cap


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


def _material_path(material: Material, settings: Settings):
    return material_file_path(material.id, settings)


def _human(
    summary: str,
    *,
    evidence: list[ReviewEvidence],
    data: dict[str, Any],
    fields: list[str],
) -> RuleExecutionResult:
    payload = dict(data)
    payload["pending_fields"] = fields
    return RuleExecutionResult(
        status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
        summary=summary,
        evidence=evidence,
        data=payload,
    )


def _to_bbox(bbox: BBox | None) -> ReviewEvidenceBBox | None:
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


def _category_evidence(material: Material, category: ExtractedCategory) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=material.id,
        category=material.category,
        original_filename=material.original_filename,
        field_name="项目类别",
        raw_value=category.raw_value,
        normalized_value=category.normalized,
        page_number=category.page_number,
        quote=category.quote,
        bbox=_to_bbox(category.bbox),
        reliable=category.reliable,
        reason=category.reason,
    )


def _funding_evidence(
    material: Material,
    extracted: ExtractedFunding,
    *,
    field_name: str,
) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=material.id,
        category=material.category,
        original_filename=material.original_filename,
        field_name=field_name,
        raw_value=extracted.raw_value,
        normalized_value=extracted.amount_yuan,
        page_number=extracted.page_number,
        quote=extracted.quote,
        bbox=_to_bbox(extracted.bbox),
        reliable=extracted.reliable,
        reason=extracted.reason,
    )


def _budget_evidence(material: Material, extracted: ExtractedBudget) -> list[ReviewEvidence]:
    items = [_funding_evidence(material, extracted.application_total, field_name="申请总额")]
    for line in extracted.lines:
        items.append(_line_evidence(material, line))
    if extracted.line_sum_yuan is not None:
        items.append(
            ReviewEvidence(
                material_id=material.id,
                category=material.category,
                original_filename=material.original_filename,
                field_name="科目合计",
                raw_value=str(extracted.line_sum_yuan),
                normalized_value=extracted.line_sum_yuan,
                reliable=extracted.reliable and not extracted.scope_uncertain,
                reason=extracted.reason,
            )
        )
    return items


def _line_evidence(material: Material, line: BudgetLine) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=material.id,
        category=material.category,
        original_filename=material.original_filename,
        field_name=line.name,
        raw_value=line.raw_value,
        normalized_value=line.amount_yuan,
        page_number=line.page_number,
        quote=line.quote,
        bbox=_to_bbox(line.bbox),
        reliable=line.reliable,
        reason=line.reason,
    )
