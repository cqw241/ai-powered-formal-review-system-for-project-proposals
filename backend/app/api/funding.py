"""Application-funding review endpoints (RULE-007, B03)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Project
from app.schemas import FundingReviewRead
from app.services import funding_review as review_service

router = APIRouter(tags=["funding-review"])


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


@router.post(
    "/api/projects/{project_id}/funding-review",
    response_model=FundingReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_funding_review(project_id: str, db: Session = Depends(get_db)) -> FundingReviewRead:
    """Run application-funding extraction + comparison and persist the finding."""
    _get_project_or_404(db, project_id)
    review = review_service.run_funding_review(db, project_id)
    return review_service.to_funding_review_read(review)


@router.get(
    "/api/projects/{project_id}/funding-review",
    response_model=FundingReviewRead,
)
def get_funding_review(project_id: str, db: Session = Depends(get_db)) -> FundingReviewRead:
    """Return the latest persisted funding review for the project."""
    _get_project_or_404(db, project_id)
    review = review_service.get_latest_funding_review(db, project_id)
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="尚未进行申请经费核对",
        )
    return review_service.to_funding_review_read(review)


@router.get(
    "/api/projects/{project_id}/funding-reviews",
    response_model=list[FundingReviewRead],
)
def list_funding_reviews(project_id: str, db: Session = Depends(get_db)) -> list[FundingReviewRead]:
    """Return funding reviews newest-first; each row keeps its bound_rules snapshot."""
    _get_project_or_404(db, project_id)
    reviews = review_service.list_funding_reviews(db, project_id)
    return [review_service.to_funding_review_read(item) for item in reviews]


@router.get(
    "/api/projects/{project_id}/funding-reviews/{review_id}",
    response_model=FundingReviewRead,
)
def get_funding_review_by_id(
    project_id: str,
    review_id: str,
    db: Session = Depends(get_db),
) -> FundingReviewRead:
    _get_project_or_404(db, project_id)
    review = review_service.get_funding_review(db, project_id, review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="核对记录不存在")
    return review_service.to_funding_review_read(review)
