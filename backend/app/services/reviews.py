"""Review tasks: built-in RULE-007 plus selected enabled-rule snapshots."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    FundingReview,
    FundingReviewStatus,
    ReviewItem,
    ReviewItemStatus,
    ReviewTask,
    ReviewTaskStatus,
    Rule,
    RuleVersion,
    utc_now,
)
from app.schemas import BoundRuleSnapshot, FundingFinding, ReviewItemRead, ReviewTaskRead
from app.services import funding_review as funding_service
from app.services.rules import bound_snapshot, current_version, load_rule

RULE_007 = "RULE-007"
RULE_007_NAME = "申请经费跨文件一致性"
RUNNING_SUMMARY = "正在执行申请经费核对"
NOT_EXECUTED_SUMMARY = (
    "本阶段无执行器，已绑定当时版本快照；不按学科类别上限判 PASS/FAIL。"
)


class RuleNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("规则不存在")


class RuleNotEnabledError(ValueError):
    def __init__(self) -> None:
        super().__init__("规则未启用，不能纳入本次审查")


def start_review(db: Session, project_id: str, rule_ids: list[str]) -> ReviewTask:
    """Persist a RUNNING task with items, run RULE-007, then store the terminal states."""
    selected = _load_selected_rules(db, rule_ids)
    task_id = _create_running_task(db, project_id, selected)
    try:
        funding = funding_service.run_funding_review(db, project_id)
    except Exception as exc:  # noqa: BLE001 — workspace item FAILED, do not leave RUNNING
        _complete_with_failure(db, project_id, task_id, f"申请经费核对失败：{exc}")
        return _require_task(db, project_id, task_id)

    _complete_with_funding(db, project_id, task_id, funding)
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


def to_review_task_read(task: ReviewTask) -> ReviewTaskRead:
    items = sorted(task.items, key=lambda item: item.sort_order)
    return ReviewTaskRead(
        id=task.id,
        project_id=task.project_id,
        status=ReviewTaskStatus(task.status),
        created_at=task.created_at,
        updated_at=task.updated_at,
        items=[_to_item_read(item) for item in items],
    )


def _require_task(db: Session, project_id: str, task_id: str) -> ReviewTask:
    loaded = get_review_task(db, project_id, task_id)
    assert loaded is not None
    return loaded


def _to_item_read(item: ReviewItem) -> ReviewItemRead:
    snapshot = None
    if item.snapshot_json:
        snapshot = BoundRuleSnapshot.model_validate(json.loads(item.snapshot_json))
    check_status = FundingReviewStatus(item.check_status) if item.check_status else None
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
    items = [
        ReviewItem(
            id=str(uuid.uuid4()),
            task_id=task.id,
            sort_order=0,
            rule_code=RULE_007,
            source_rule_id=None,
            version_id=None,
            version_number=None,
            name=RULE_007_NAME,
            status=ReviewItemStatus.RUNNING.value,
            check_status=None,
            summary=RUNNING_SUMMARY,
            snapshot_json=None,
            funding_review_id=None,
        )
    ]
    for index, (rule, version) in enumerate(selected, start=1):
        snapshot = bound_snapshot(rule, version, application_amount_yuan=None)
        items.append(
            ReviewItem(
                id=str(uuid.uuid4()),
                task_id=task.id,
                sort_order=index,
                rule_code=rule.rule_code,
                source_rule_id=rule.id,
                version_id=version.id,
                version_number=version.version_number,
                name=version.name or rule.name,
                status=ReviewItemStatus.NOT_EXECUTED.value,
                check_status=None,
                summary=NOT_EXECUTED_SUMMARY,
                snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                funding_review_id=None,
            )
        )
    db.add(task)
    db.add_all(items)
    db.commit()
    return task.id


def _complete_with_funding(
    db: Session,
    project_id: str,
    task_id: str,
    funding: FundingReview,
) -> None:
    task = _require_task(db, project_id, task_id)
    read = funding_service.to_funding_review_read(funding)
    item_status, default_summary = _map_funding_status(read.status)
    rule007 = _rule007_item(task)
    rule007.status = item_status.value
    rule007.check_status = read.status.value
    rule007.summary = read.finding.reason or default_summary
    rule007.funding_review_id = funding.id
    application_amount = _application_amount(read.finding)
    for item in task.items:
        if item.rule_code == RULE_007 or not item.snapshot_json:
            continue
        snapshot = json.loads(item.snapshot_json)
        snapshot["application_amount_yuan"] = application_amount
        item.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    task.status = ReviewTaskStatus.COMPLETED.value
    task.updated_at = utc_now()
    db.commit()


def _complete_with_failure(db: Session, project_id: str, task_id: str, summary: str) -> None:
    db.rollback()
    task = _require_task(db, project_id, task_id)
    rule007 = _rule007_item(task)
    rule007.status = ReviewItemStatus.FAILED.value
    rule007.check_status = FundingReviewStatus.SYSTEM_ERROR.value
    rule007.summary = summary
    task.status = ReviewTaskStatus.COMPLETED.value
    task.updated_at = utc_now()
    db.commit()


def _rule007_item(task: ReviewTask) -> ReviewItem:
    for item in task.items:
        if item.rule_code == RULE_007:
            return item
    raise LookupError("审查任务缺少 RULE-007 条目")


def _map_funding_status(status: FundingReviewStatus) -> tuple[ReviewItemStatus, str]:
    if status in (FundingReviewStatus.PASS, FundingReviewStatus.FAIL):
        return ReviewItemStatus.COMPLETED, "已完成申请经费核对"
    if status == FundingReviewStatus.NEED_HUMAN_REVIEW:
        return ReviewItemStatus.PENDING_CONFIRMATION, "申请经费核对待确认"
    return ReviewItemStatus.FAILED, "申请经费核对失败"


def _application_amount(finding: FundingFinding) -> int | None:
    left = finding.left
    if left is not None and left.reliable and left.amount_yuan is not None:
        return int(left.amount_yuan)
    return None
