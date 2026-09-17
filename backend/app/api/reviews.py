"""Review-task endpoints (B06 workspace)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Project
from app.schemas import HumanDecisionCreate, ReviewTaskCreate, ReviewTaskRead
from app.services import reviews as review_service
from app.services.human_resolution import (
    HumanResolutionError,
    ItemNotFoundError,
    TaskNotFoundError,
    apply_human_decision,
)
from app.services.reviews import RuleNotEnabledError, RuleNotFoundError

router = APIRouter(tags=["reviews"])


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


@router.post(
    "/api/projects/{project_id}/reviews",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    project_id: str,
    body: ReviewTaskCreate,
    db: Session = Depends(get_db),
) -> ReviewTaskRead:
    _get_project_or_404(db, project_id)
    try:
        task = review_service.start_review(db, project_id, body.rule_ids)
    except RuleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuleNotEnabledError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return review_service.to_review_task_read(task, db)


@router.get(
    "/api/projects/{project_id}/reviews",
    response_model=list[ReviewTaskRead],
)
def list_reviews(project_id: str, db: Session = Depends(get_db)) -> list[ReviewTaskRead]:
    _get_project_or_404(db, project_id)
    tasks = review_service.list_review_tasks(db, project_id)
    return [review_service.to_review_task_read(item, db) for item in tasks]


@router.get(
    "/api/projects/{project_id}/reviews/{task_id}",
    response_model=ReviewTaskRead,
)
def get_review(project_id: str, task_id: str, db: Session = Depends(get_db)) -> ReviewTaskRead:
    _get_project_or_404(db, project_id)
    task = review_service.get_review_task(db, project_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审查任务不存在")
    return review_service.to_review_task_read(task, db)


@router.post(
    "/api/projects/{project_id}/reviews/{task_id}/items/{item_id}/human-decisions",
    response_model=ReviewTaskRead,
)
def create_human_decision(
    project_id: str,
    task_id: str,
    item_id: str,
    body: HumanDecisionCreate,
    db: Session = Depends(get_db),
) -> ReviewTaskRead:
    _get_project_or_404(db, project_id)
    try:
        task = apply_human_decision(db, project_id, task_id, item_id, body)
    except (TaskNotFoundError, ItemNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HumanResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return review_service.to_review_task_read(task, db)
