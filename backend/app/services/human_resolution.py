"""Human confirmation, field correction, and not-applicable marks on a review task.

Machine payloads in ``review_item_results`` stay frozen. Effective results after
human actions live in ``review_item_human_results``. Field corrections re-run
existing rule executors with accumulated overrides; they do not hand-edit verdicts.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    HumanDecision,
    HumanDecisionAction,
    Material,
    ReviewItem,
    ReviewItemHumanResult,
    ReviewItemStatus,
    ReviewTask,
    ReviewTaskStatus,
    utc_now,
)
from app.review_contract import FieldOverride, ReviewCheckStatus, RuleExecutionResult
from app.schemas import HumanDecisionCreate
from app.services.field_overrides import AMOUNT_FIELDS, affected_rule_codes, canonical_field
from app.services.money import parse_amount_text
from app.services.review_results import item_status_for_check, load_rule_result


class TaskNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("审查任务不存在")


class ItemNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("审查条目不存在")


class HumanResolutionError(ValueError):
    pass


def persist_human_item_result(
    db: Session,
    item: ReviewItem,
    result: RuleExecutionResult,
    *,
    decision_id: str,
    item_status: ReviewItemStatus | None = None,
    commit: bool = False,
) -> None:
    """Upsert the effective human result without touching machine review_item_results."""
    payload_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    now = utc_now()
    existing = db.get(ReviewItemHumanResult, item.id)
    if existing is None:
        db.add(
            ReviewItemHumanResult(
                review_item_id=item.id,
                decision_id=decision_id,
                payload_json=payload_json,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        existing.decision_id = decision_id
        existing.payload_json = payload_json
        existing.updated_at = now
        db.add(existing)

    mapped = item_status_for_check(result.status)
    item.status = (item_status or mapped).value
    item.check_status = result.status.value
    item.summary = result.summary
    db.add(item)
    if commit:
        db.commit()


def apply_human_decision(
    db: Session,
    project_id: str,
    task_id: str,
    item_id: str,
    payload: HumanDecisionCreate,
) -> ReviewTask:
    from app.services.reviews import get_review_task

    task = get_review_task(db, project_id, task_id)
    if task is None:
        raise TaskNotFoundError()
    if task.status != ReviewTaskStatus.COMPLETED.value:
        raise HumanResolutionError("审查任务尚未完成，不能人工处置")
    item = next((row for row in task.items if row.id == item_id), None)
    if item is None:
        raise ItemNotFoundError()
    if item.status in {ReviewItemStatus.RUNNING.value, ReviewItemStatus.NOT_EXECUTED.value}:
        raise HumanResolutionError("该条目尚未完成执行，不能人工处置")

    if payload.action == HumanDecisionAction.CORRECT_FIELD:
        _apply_field_correction(db, project_id, task, item, payload)
    elif payload.action == HumanDecisionAction.MARK_NOT_APPLICABLE:
        _apply_not_applicable(db, task, item, payload)
    else:
        _apply_confirm(db, task, item, payload)

    task.updated_at = utc_now()
    db.add(task)
    db.commit()
    loaded = get_review_task(db, project_id, task_id)
    assert loaded is not None
    return loaded


def list_task_decisions(db: Session, task_id: str) -> list[HumanDecision]:
    statement = (
        select(HumanDecision)
        .where(HumanDecision.task_id == task_id)
        .order_by(HumanDecision.created_at.asc())
    )
    return list(db.scalars(statement).all())


def load_human_results(db: Session, item_ids: list[str]) -> dict[str, RuleExecutionResult]:
    if not item_ids:
        return {}
    statement = select(ReviewItemHumanResult).where(
        ReviewItemHumanResult.review_item_id.in_(item_ids)
    )
    loaded: dict[str, RuleExecutionResult] = {}
    for row in db.scalars(statement).all():
        loaded[row.review_item_id] = RuleExecutionResult.model_validate(json.loads(row.payload_json))
    return loaded


def decisions_for_item(item: ReviewItem, decisions: list[HumanDecision]) -> list[HumanDecision]:
    matched: list[HumanDecision] = []
    for decision in decisions:
        affected = json.loads(decision.affected_rule_codes_json or "[]")
        if decision.review_item_id == item.id or item.rule_code in affected:
            matched.append(decision)
    return matched


def accumulated_overrides(decisions: list[HumanDecision]) -> list[FieldOverride]:
    """Latest CORRECT_FIELD value per (material_id, canonical field)."""
    latest: dict[tuple[str, str], FieldOverride] = {}
    for decision in decisions:
        if decision.action != HumanDecisionAction.CORRECT_FIELD.value:
            continue
        if not decision.material_id or not decision.field_name or decision.corrected_value is None:
            continue
        override = FieldOverride(
            material_id=decision.material_id,
            field_name=decision.field_name,
            raw_value=decision.corrected_value,
        )
        latest[(decision.material_id, canonical_field(decision.field_name))] = override
    return list(latest.values())


def _apply_confirm(
    db: Session,
    task: ReviewTask,
    item: ReviewItem,
    payload: HumanDecisionCreate,
) -> None:
    original = load_rule_result(db, item.id)
    decision = _add_decision(
        db,
        task=task,
        item=item,
        payload=payload,
        original_value=_original_status_value(original, item),
        corrected_value=None,
        affected=[item.rule_code],
    )
    result = _overlay_result(
        original,
        item,
        status=ReviewCheckStatus(item.check_status) if item.check_status else ReviewCheckStatus.NEED_HUMAN_REVIEW,
        summary=item.summary or "已确认该条审查结论",
        human_action=HumanDecisionAction.CONFIRM.value,
    )
    persist_human_item_result(
        db,
        item,
        result,
        decision_id=decision.id,
        item_status=ReviewItemStatus.COMPLETED,
        commit=False,
    )


def _apply_not_applicable(
    db: Session,
    task: ReviewTask,
    item: ReviewItem,
    payload: HumanDecisionCreate,
) -> None:
    original = load_rule_result(db, item.id)
    decision = _add_decision(
        db,
        task=task,
        item=item,
        payload=payload,
        original_value=_original_status_value(original, item),
        corrected_value=ReviewCheckStatus.NOT_APPLICABLE.value,
        affected=[item.rule_code],
    )
    summary = payload.note
    result = _overlay_result(
        original,
        item,
        status=ReviewCheckStatus.NOT_APPLICABLE,
        summary=f"人工标记不适用：{summary}",
        human_action=HumanDecisionAction.MARK_NOT_APPLICABLE.value,
    )
    persist_human_item_result(db, item, result, decision_id=decision.id, commit=False)


def _apply_field_correction(
    db: Session,
    project_id: str,
    task: ReviewTask,
    item: ReviewItem,
    payload: HumanDecisionCreate,
) -> None:
    field_name = (payload.field_name or "").strip()
    material_id = (payload.material_id or "").strip()
    corrected = (payload.corrected_value or "").strip()
    if not field_name or not material_id or not corrected:
        raise HumanResolutionError("修正字段需要提供 field_name、material_id 和 corrected_value")

    material = db.get(Material, material_id)
    if material is None or material.project_id != project_id:
        raise HumanResolutionError("修正字段对应的材料不存在")

    category = material.category
    codes = affected_rule_codes(field_name, category)
    if not codes:
        raise HumanResolutionError("该字段没有可重算的规则，不能手工覆盖规则结论")
    if field_name in AMOUNT_FIELDS and parse_amount_text(corrected, default_unit="yuan") is None:
        raise HumanResolutionError("无法解析修正后的金额，未知值不能当作 0")

    original = load_rule_result(db, item.id)
    original_value = _original_field_value(original, material_id, field_name)
    pending = list_task_decisions(db, task.id)
    decision = _add_decision(
        db,
        task=task,
        item=item,
        payload=payload,
        original_value=original_value,
        corrected_value=corrected,
        affected=codes,
    )
    pending.append(decision)
    overrides = accumulated_overrides(pending)

    targets = [
        row
        for row in task.items
        if row.rule_code in codes and row.status != ReviewItemStatus.NOT_EXECUTED.value
    ]
    if not targets:
        raise HumanResolutionError("本次审查没有可重算的受影响规则")

    from app.services.reviews import execute_item_with_overrides

    for target in targets:
        try:
            result = execute_item_with_overrides(db, project_id, target, overrides)
        except Exception as exc:  # noqa: BLE001
            result = RuleExecutionResult(
                status=ReviewCheckStatus.SYSTEM_ERROR,
                summary=f"{target.rule_code} 按人工修正重算失败：{exc}",
                data={"rule_code": target.rule_code, "error": str(exc)},
            )
        data = dict(result.data)
        data["human_action"] = HumanDecisionAction.CORRECT_FIELD.value
        data["recomputed"] = True
        result = result.model_copy(update={"data": data})
        persist_human_item_result(db, target, result, decision_id=decision.id, commit=False)


def _add_decision(
    db: Session,
    *,
    task: ReviewTask,
    item: ReviewItem,
    payload: HumanDecisionCreate,
    original_value: str | None,
    corrected_value: str | None,
    affected: list[str],
) -> HumanDecision:
    decision = HumanDecision(
        id=str(uuid.uuid4()),
        task_id=task.id,
        review_item_id=item.id,
        action=payload.action.value,
        operator=payload.operator,
        note=payload.note,
        field_name=payload.field_name,
        material_id=payload.material_id,
        original_value=original_value,
        corrected_value=corrected_value,
        affected_rule_codes_json=json.dumps(affected, ensure_ascii=False),
    )
    db.add(decision)
    db.flush()
    return decision


def _overlay_result(
    original: RuleExecutionResult | None,
    item: ReviewItem,
    *,
    status: ReviewCheckStatus,
    summary: str,
    human_action: str,
) -> RuleExecutionResult:
    evidence = list(original.evidence) if original is not None else []
    data = dict(original.data) if original is not None else {"rule_code": item.rule_code}
    data["human_action"] = human_action
    data["machine_status"] = (
        original.status.value if original is not None else item.check_status
    )
    return RuleExecutionResult(
        status=status,
        summary=summary,
        evidence=evidence,
        data=data,
    )


def _original_status_value(original: RuleExecutionResult | None, item: ReviewItem) -> str | None:
    if original is not None:
        return original.status.value
    return item.check_status


def _original_field_value(
    original: RuleExecutionResult | None,
    material_id: str,
    field_name: str,
) -> str | None:
    if original is None:
        return None
    wanted = canonical_field(field_name)
    for evidence in original.evidence:
        if evidence.material_id != material_id:
            continue
        if canonical_field(evidence.field_name or "") != wanted:
            continue
        if evidence.raw_value:
            return evidence.raw_value
        if evidence.normalized_value is not None:
            return str(evidence.normalized_value)
    return None
