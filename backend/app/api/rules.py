"""Rule enable/disable, version edits, and listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PolicyCandidate, PolicyDocument
from app.schemas import RuleRead, RuleUpdate
from app.services import rules as rule_service

router = APIRouter(tags=["rules"])


def _get_rule_or_404(db: Session, rule_id: str):
    rule = rule_service.load_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规则不存在")
    return rule


@router.get("/api/rules", response_model=list[RuleRead])
def list_rules(db: Session = Depends(get_db)) -> list[RuleRead]:
    return [RuleRead.model_validate(item) for item in rule_service.list_rules(db)]


@router.get("/api/rules/{rule_id}", response_model=RuleRead)
def get_rule(rule_id: str, db: Session = Depends(get_db)) -> RuleRead:
    return RuleRead.model_validate(_get_rule_or_404(db, rule_id))


@router.post(
    "/api/policies/{policy_id}/candidates/{candidate_id}/enable",
    response_model=RuleRead,
)
def enable_candidate_rule(
    policy_id: str,
    candidate_id: str,
    db: Session = Depends(get_db),
) -> RuleRead:
    policy = db.get(PolicyDocument, policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="政策不存在")
    candidate = db.get(PolicyCandidate, candidate_id)
    if candidate is None or candidate.policy_id != policy_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选要求不存在")
    try:
        rule = rule_service.enable_from_candidate(db, policy, candidate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RuleRead.model_validate(rule)


@router.post("/api/rules/{rule_id}/enable", response_model=RuleRead)
def enable_rule(rule_id: str, db: Session = Depends(get_db)) -> RuleRead:
    rule = _get_rule_or_404(db, rule_id)
    return RuleRead.model_validate(rule_service.set_enabled(db, rule, True))


@router.post("/api/rules/{rule_id}/disable", response_model=RuleRead)
def disable_rule(rule_id: str, db: Session = Depends(get_db)) -> RuleRead:
    rule = _get_rule_or_404(db, rule_id)
    return RuleRead.model_validate(rule_service.set_enabled(db, rule, False))


@router.patch("/api/rules/{rule_id}", response_model=RuleRead)
def patch_rule(rule_id: str, body: RuleUpdate, db: Session = Depends(get_db)) -> RuleRead:
    rule = _get_rule_or_404(db, rule_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return RuleRead.model_validate(rule)
    updated = rule_service.update_rule(db, rule, patch)
    return RuleRead.model_validate(updated)
