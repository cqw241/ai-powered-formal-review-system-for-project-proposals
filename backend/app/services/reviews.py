"""Review tasks: built-in W3 rules plus selected enabled-rule snapshots."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    FundingReviewStatus,
    ReviewItem,
    ReviewItemStatus,
    ReviewTask,
    ReviewTaskStatus,
    Rule,
    RuleVersion,
    utc_now,
)
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    ReviewEvidenceBBox,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.schemas import BoundRuleSnapshot, FundingFinding, FundingReviewRead, ReviewItemRead, ReviewTaskRead
from app.services import funding_review as funding_service
from app.services.budget_review import execute_budget_rule
from app.services.date_review import execute_date_rule
from app.services.identity_review import execute_identity_rule
from app.services.review_results import load_rule_result, persist_rule_result
from app.services.rules import bound_snapshot, current_version, load_rule

RULE_007 = "RULE-007"
NOT_EXECUTED_SUMMARY = "本阶段无执行器，已绑定当时版本快照。"
BUILTIN_RULES: tuple[tuple[str, str], ...] = (
    ("RULE-002", "项目名称跨文件一致"),
    ("RULE-003", "项目负责人跨文件一致"),
    ("RULE-004", "项目周期窗口与时长"),
    ("RULE-006", "预算科目合计一致"),
    ("RULE-007", "申请经费跨文件一致性"),
    ("RULE-010", "承诺书签署日期"),
)
EXECUTORS: dict[str, Callable[[Session, RuleExecutionContext], RuleExecutionResult]] = {
    "RULE-002": execute_identity_rule,
    "RULE-003": execute_identity_rule,
    "RULE-004": execute_date_rule,
    "RULE-005": execute_budget_rule,
    "RULE-006": execute_budget_rule,
    "RULE-010": execute_date_rule,
}


class RuleNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("规则不存在")


class RuleNotEnabledError(ValueError):
    def __init__(self) -> None:
        super().__init__("规则未启用，不能纳入本次审查")


def start_review(db: Session, project_id: str, rule_ids: list[str]) -> ReviewTask:
    """Persist a RUNNING task, execute built-in + selected rules, then store terminal states."""
    selected = _load_selected_rules(db, rule_ids)
    task_id = _create_running_task(db, project_id, selected)
    task = _require_task(db, project_id, task_id)
    try:
        for item in sorted(task.items, key=lambda row: row.sort_order):
            _run_item(db, project_id, item)
            db.commit()
            db.refresh(item)
        task = _require_task(db, project_id, task_id)
        task.status = ReviewTaskStatus.COMPLETED.value
        task.updated_at = utc_now()
        db.commit()
    except Exception as exc:  # noqa: BLE001 — do not leave RUNNING items
        _fail_remaining(db, project_id, task_id, f"审查任务中断：{exc}")
    return _require_task(db, project_id, task_id)


def list_review_tasks(db: Session, project_id: str) -> list[ReviewTask]:
    statement = (
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id)
        .options(selectinload(ReviewTask.items))
        .order_by(ReviewTask.created_at.desc())
    )
    return list(db.scalars(statement).all())


def get_review_task(db: Session, project_id: str, task_id: str) -> ReviewTask | None:
    statement = (
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id, ReviewTask.id == task_id)
        .options(selectinload(ReviewTask.items))
    )
    return db.scalars(statement).first()


def to_review_task_read(task: ReviewTask, db: Session | None = None) -> ReviewTaskRead:
    items = sorted(task.items, key=lambda item: item.sort_order)
    return ReviewTaskRead(
        id=task.id,
        project_id=task.project_id,
        status=ReviewTaskStatus(task.status),
        created_at=task.created_at,
        updated_at=task.updated_at,
        items=[_to_item_read(item, db) for item in items],
    )


def _require_task(db: Session, project_id: str, task_id: str) -> ReviewTask:
    loaded = get_review_task(db, project_id, task_id)
    assert loaded is not None
    return loaded


def _to_item_read(item: ReviewItem, db: Session | None) -> ReviewItemRead:
    snapshot = None
    if item.snapshot_json:
        snapshot = BoundRuleSnapshot.model_validate(json.loads(item.snapshot_json))
    check_status = FundingReviewStatus(item.check_status) if item.check_status else None
    result = load_rule_result(db, item.id) if db is not None else None
    return ReviewItemRead(
        id=item.id,
        rule_code=item.rule_code,
        source_rule_id=item.source_rule_id,
        name=item.name,
        status=ReviewItemStatus(item.status),
        check_status=check_status,
        summary=item.summary,
        version_id=item.version_id,
        version_number=item.version_number,
        snapshot=snapshot,
        funding_review_id=item.funding_review_id,
        sort_order=item.sort_order,
        result=result,
    )


def _load_selected_rules(db: Session, rule_ids: list[str]) -> list[tuple[Rule, RuleVersion]]:
    selected: list[tuple[Rule, RuleVersion]] = []
    seen: set[str] = set()
    for rule_id in rule_ids:
        cleaned = rule_id.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rule = load_rule(db, cleaned)
        if rule is None:
            raise RuleNotFoundError()
        if not rule.enabled:
            raise RuleNotEnabledError()
        selected.append((rule, current_version(rule)))
    return selected


def _create_running_task(
    db: Session,
    project_id: str,
    selected: list[tuple[Rule, RuleVersion]],
) -> str:
    task = ReviewTask(
        id=str(uuid.uuid4()),
        project_id=project_id,
        status=ReviewTaskStatus.RUNNING.value,
    )
    builtin_codes = {code for code, _name in BUILTIN_RULES}
    items = [
        ReviewItem(
            id=str(uuid.uuid4()),
            task_id=task.id,
            sort_order=0,
            rule_code=code,
            source_rule_id=None,
            version_id=None,
            version_number=None,
            name=name,
            status=ReviewItemStatus.RUNNING.value,
            check_status=None,
            summary=f"正在执行{name}",
            snapshot_json=None,
            funding_review_id=None,
        )
        for code, name in BUILTIN_RULES
    ]
    for rule, version in selected:
        if rule.rule_code in builtin_codes:
            continue
        snapshot = bound_snapshot(rule, version, application_amount_yuan=None)
        has_executor = rule.rule_code in EXECUTORS
        items.append(
            ReviewItem(
                id=str(uuid.uuid4()),
                task_id=task.id,
                sort_order=0,
                rule_code=rule.rule_code,
                source_rule_id=rule.id,
                version_id=version.id,
                version_number=version.version_number,
                name=version.name or rule.name,
                status=(
                    ReviewItemStatus.RUNNING.value
                    if has_executor
                    else ReviewItemStatus.NOT_EXECUTED.value
                ),
                check_status=None,
                summary=(
                    f"正在执行{version.name or rule.name}"
                    if has_executor
                    else NOT_EXECUTED_SUMMARY
                ),
                snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                funding_review_id=None,
            )
        )
    items.sort(key=lambda item: (item.rule_code, item.source_rule_id or ""))
    for index, item in enumerate(items):
        item.sort_order = index
    db.add(task)
    db.add_all(items)
    db.commit()
    return task.id


def _run_item(db: Session, project_id: str, item: ReviewItem) -> None:
    if item.status == ReviewItemStatus.NOT_EXECUTED.value:
        return
    if item.rule_code == RULE_007:
        _run_rule_007(db, project_id, item)
        return
    executor = EXECUTORS.get(item.rule_code)
    if executor is None:
        item.status = ReviewItemStatus.NOT_EXECUTED.value
        item.summary = NOT_EXECUTED_SUMMARY
        db.add(item)
        return
    snapshot = json.loads(item.snapshot_json) if item.snapshot_json else None
    context = RuleExecutionContext(
        project_id=project_id,
        review_item_id=item.id,
        rule_code=item.rule_code,
        source_rule_id=item.source_rule_id,
        version_id=item.version_id,
        version_number=item.version_number,
        snapshot=snapshot,
    )
    try:
        result = executor(db, context)
    except Exception as exc:  # noqa: BLE001
        result = RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"{item.rule_code} 执行失败：{exc}",
            data={"rule_code": item.rule_code, "error": str(exc)},
        )
    persist_rule_result(db, item, result, commit=False)


def _run_rule_007(db: Session, project_id: str, item: ReviewItem) -> None:
    try:
        funding = funding_service.run_funding_review(db, project_id)
    except Exception as exc:  # noqa: BLE001
        persist_rule_result(
            db,
            item,
            RuleExecutionResult(
                status=ReviewCheckStatus.SYSTEM_ERROR,
                summary=f"申请经费核对失败：{exc}",
                data={"rule_code": RULE_007, "error": str(exc)},
            ),
            commit=False,
        )
        return
    read = funding_service.to_funding_review_read(funding)
    item.funding_review_id = funding.id
    persist_rule_result(db, item, _funding_as_result(read), commit=False)
    application_amount = _application_amount(read.finding)
    task = _require_task(db, project_id, item.task_id)
    for other in task.items:
        if other.rule_code == RULE_007 or not other.snapshot_json:
            continue
        snapshot = json.loads(other.snapshot_json)
        snapshot["application_amount_yuan"] = application_amount
        other.snapshot_json = json.dumps(snapshot, ensure_ascii=False)


def _funding_as_result(read: FundingReviewRead) -> RuleExecutionResult:
    finding = read.finding
    evidence: list[ReviewEvidence] = []
    for side, field_name in (
        (finding.left, "申报书申请经费"),
        (finding.right, "预算申请总额"),
    ):
        if side is None:
            continue
        bbox = None
        if side.bbox is not None:
            bbox = ReviewEvidenceBBox(
                x0=side.bbox.x0,
                y0=side.bbox.y0,
                x1=side.bbox.x1,
                y1=side.bbox.y1,
                page_width=side.bbox.page_width,
                page_height=side.bbox.page_height,
            )
        evidence.append(
            ReviewEvidence(
                material_id=side.material_id,
                category=side.category.value if side.category else None,
                original_filename=side.original_filename,
                field_name=field_name,
                raw_value=side.raw_value,
                normalized_value=side.amount_yuan,
                page_number=side.page_number,
                quote=side.quote,
                bbox=bbox,
                reliable=side.reliable,
                reason=side.reason,
            )
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus(read.status.value),
        summary=finding.reason or "已完成申请经费核对",
        evidence=evidence,
        data={
            "rule_code": RULE_007,
            "difference_yuan": finding.difference_yuan,
            "funding_review_id": read.id,
        },
    )


def _fail_remaining(db: Session, project_id: str, task_id: str, summary: str) -> None:
    db.rollback()
    task = _require_task(db, project_id, task_id)
    for item in task.items:
        if item.status != ReviewItemStatus.RUNNING.value:
            continue
        persist_rule_result(
            db,
            item,
            RuleExecutionResult(
                status=ReviewCheckStatus.SYSTEM_ERROR,
                summary=summary,
                data={"rule_code": item.rule_code},
            ),
            commit=False,
        )
    task.status = ReviewTaskStatus.COMPLETED.value
    task.updated_at = utc_now()
    db.commit()


def _application_amount(finding: FundingFinding) -> int | None:
    left = finding.left
    if left is not None and left.reliable and left.amount_yuan is not None:
        return int(left.amount_yuan)
    return None
