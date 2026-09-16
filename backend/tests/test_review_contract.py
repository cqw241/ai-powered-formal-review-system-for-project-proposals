"""W3 shared executor/result contract and persistence tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Project, ReviewItem, ReviewItemStatus, ReviewTask, ReviewTaskStatus
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.services.review_results import (
    load_rule_result,
    persist_rule_result,
    review_item_results,
)


def _new_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return Session(engine)


def _seed_item(db: Session) -> ReviewItem:
    project_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    item = ReviewItem(
        id=str(uuid.uuid4()),
        task_id=task_id,
        sort_order=0,
        rule_code="RULE-002",
        source_rule_id=None,
        version_id=None,
        version_number=None,
        name="项目名称跨文件一致",
        status=ReviewItemStatus.RUNNING.value,
        check_status=None,
        summary="执行中",
        snapshot_json=None,
        funding_review_id=None,
    )
    db.add(Project(id=project_id, name="W3 contract test"))
    db.add(
        ReviewTask(
            id=task_id,
            project_id=project_id,
            status=ReviewTaskStatus.RUNNING.value,
        )
    )
    db.add(item)
    db.commit()
    return item


@pytest.mark.parametrize(
    ("check_status", "item_status"),
    [
        ("PASS", ReviewItemStatus.COMPLETED),
        ("FAIL", ReviewItemStatus.COMPLETED),
        ("NEED_HUMAN_REVIEW", ReviewItemStatus.PENDING_CONFIRMATION),
        ("NOT_APPLICABLE", ReviewItemStatus.COMPLETED),
        ("SYSTEM_ERROR", ReviewItemStatus.FAILED),
    ],
)
def test_result_status_maps_to_workspace_state(check_status: str, item_status: ReviewItemStatus):
    db = _new_db()
    item = _seed_item(db)
    result = RuleExecutionResult(status=check_status, summary=f"status={check_status}")

    persist_rule_result(db, item, result, commit=True)
    db.refresh(item)

    assert item.status == item_status.value
    assert item.check_status == check_status
    assert item.summary == result.summary
    db.close()


def test_result_payload_round_trips_and_upserts_one_row():
    db = _new_db()
    item = _seed_item(db)
    first = RuleExecutionResult(
        status="FAIL",
        summary="项目名称不一致",
        evidence=[
            ReviewEvidence(
                material_id="material-a",
                category="APPLICATION",
                field_name="项目名称",
                raw_value="项目 A",
                normalized_value="项目 A",
                page_number=1,
                quote="项目名称：项目 A",
                reliable=True,
            )
        ],
        data={"normalized_values": ["项目 A", "项目 B"]},
    )
    persist_rule_result(db, item, first, commit=True)

    loaded = load_rule_result(db, item.id)
    assert loaded == first

    second = RuleExecutionResult(
        status="PASS",
        summary="项目名称一致",
        data={"normalized_values": ["项目 A", "项目 A"]},
    )
    persist_rule_result(db, item, second, commit=True)

    assert load_rule_result(db, item.id) == second
    row_count = db.scalar(select(func.count()).select_from(review_item_results))
    assert row_count == 1
    db.close()


def test_execution_context_carries_only_integration_inputs():
    context = RuleExecutionContext(
        project_id="project-1",
        review_item_id="item-1",
        rule_code="RULE-005",
        source_rule_id="rule-1",
        version_id="version-2",
        version_number=2,
        snapshot={"category": "人文社会科学类", "amount_yuan": 150000},
    )

    assert context.rule_code == "RULE-005"
    assert context.snapshot == {"category": "人文社会科学类", "amount_yuan": 150000}
