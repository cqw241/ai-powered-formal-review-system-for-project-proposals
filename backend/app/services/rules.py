"""Enable, version, and snapshot RULE-005 from FUNDING_CAP drafts."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PolicyCandidate, PolicyDocument, Rule, RuleVersion
from app.services.money import parse_amount_text

RULE_005 = "RULE-005"
COMPARE_FIELD = "申请经费"
DISPLAY_NOTE = "本阶段不按项目学科类别裁决；缺项目类别不判 FAIL。仅展示绑定的类别与上限版本。"


def load_rule(db: Session, rule_id: str) -> Rule | None:
    statement = (
        select(Rule)
        .where(Rule.id == rule_id)
        .options(selectinload(Rule.versions))
    )
    return db.scalars(statement).first()


def list_rules(db: Session) -> list[Rule]:
    statement = select(Rule).options(selectinload(Rule.versions)).order_by(Rule.created_at.desc())
    return list(db.scalars(statement).all())


def current_version(rule: Rule) -> RuleVersion:
    if not rule.versions:
        raise ValueError("规则没有版本记录")
    match = next(
        (item for item in rule.versions if item.version_number == rule.current_version_number),
        None,
    )
    if match is not None:
        return match
    return max(rule.versions, key=lambda item: item.version_number)


def enable_from_candidate(
    db: Session,
    policy: PolicyDocument,
    candidate: PolicyCandidate,
) -> Rule:
    """Create Rule+v1 from a FUNDING_CAP draft, or re-enable the existing rule."""
    if candidate.kind != "FUNDING_CAP":
        raise ValueError("仅经费上限候选可启用为 RULE-005")

    existing = db.scalars(
        select(Rule)
        .where(Rule.source_candidate_id == candidate.id)
        .options(selectinload(Rule.versions))
    ).first()
    if existing is not None:
        existing.enabled = True
        existing.name = existing.name or _rule_name(candidate)
        db.add(existing)
        db.commit()
        loaded = load_rule(db, existing.id)
        assert loaded is not None
        return loaded

    name = _rule_name(candidate)
    rule_id = str(uuid.uuid4())
    rule = Rule(
        id=rule_id,
        rule_code=RULE_005,
        name=name,
        enabled=True,
        source_candidate_id=candidate.id,
        policy_id=policy.id,
        current_version_number=1,
    )
    version = _new_version(
        rule_id=rule_id,
        version_number=1,
        name=name,
        category=candidate.category,
        comparator=candidate.comparator or "LE",
        amount_yuan=candidate.amount_yuan,
        amount_raw=candidate.amount_raw,
        source_clause=candidate.source_clause,
        source_page=candidate.source_page,
        source_quote=candidate.source_quote,
        policy_id=policy.id,
    )
    db.add(rule)
    db.add(version)
    db.commit()
    loaded = load_rule(db, rule_id)
    assert loaded is not None
    return loaded


def set_enabled(db: Session, rule: Rule, enabled: bool) -> Rule:
    rule.enabled = enabled
    db.add(rule)
    db.commit()
    loaded = load_rule(db, rule.id)
    assert loaded is not None
    return loaded


def update_rule(db: Session, rule: Rule, patch: dict[str, Any]) -> Rule:
    """Append a new version; do not mutate historical versions."""
    previous = current_version(rule)
    name = previous.name
    if "name" in patch and patch["name"] is not None:
        name = patch["name"]
    category = previous.category
    if "category" in patch:
        category = patch["category"]
    comparator = previous.comparator
    if "comparator" in patch:
        comparator = patch["comparator"]
    source_clause = previous.source_clause
    if "source_clause" in patch:
        source_clause = patch["source_clause"]
    source_page = previous.source_page
    if "source_page" in patch:
        source_page = patch["source_page"]
    source_quote = previous.source_quote
    if "source_quote" in patch:
        source_quote = patch["source_quote"]

    amount_raw = previous.amount_raw
    amount_yuan = previous.amount_yuan
    if "amount_raw" in patch:
        amount_raw, amount_yuan = _normalize_amount(patch["amount_raw"])

    next_number = previous.version_number + 1
    version = _new_version(
        rule_id=rule.id,
        version_number=next_number,
        name=name,
        category=category,
        comparator=comparator,
        amount_yuan=amount_yuan,
        amount_raw=amount_raw,
        source_clause=source_clause,
        source_page=source_page,
        source_quote=source_quote,
        policy_id=previous.policy_id or rule.policy_id,
    )
    rule.name = name
    rule.current_version_number = next_number
    db.add(rule)
    db.add(version)
    db.commit()
    loaded = load_rule(db, rule.id)
    assert loaded is not None
    return loaded


def snapshot_enabled_rules(
    db: Session,
    *,
    application_amount_yuan: int | None = None,
) -> list[dict[str, Any]]:
    statement = (
        select(Rule)
        .where(Rule.enabled.is_(True))
        .options(selectinload(Rule.versions))
        .order_by(Rule.created_at.asc())
    )
    snapshots: list[dict[str, Any]] = []
    for rule in db.scalars(statement).all():
        version = current_version(rule)
        snapshots.append(bound_snapshot(rule, version, application_amount_yuan=application_amount_yuan))
    return snapshots


def bound_snapshot(
    rule: Rule,
    version: RuleVersion,
    *,
    application_amount_yuan: int | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule.id,
        "version_id": version.id,
        "version_number": version.version_number,
        "rule_code": rule.rule_code,
        "name": version.name,
        "category": version.category,
        "compare_field": version.compare_field,
        "comparator": version.comparator,
        "amount_yuan": version.amount_yuan,
        "amount_raw": version.amount_raw,
        "source_clause": version.source_clause,
        "source_page": version.source_page,
        "source_quote": version.source_quote,
        "policy_id": version.policy_id or rule.policy_id,
        "application_amount_yuan": application_amount_yuan,
        "display_note": DISPLAY_NOTE,
    }


def _rule_name(candidate: PolicyCandidate) -> str:
    title = (candidate.title or "分类申请经费上限").strip() or "分类申请经费上限"
    if candidate.category and candidate.category not in title:
        return f"{title}（{candidate.category}）"
    return title


def _normalize_amount(amount_raw: str | None) -> tuple[str | None, int | None]:
    """Unknown stays None; never coerce to 0."""
    if amount_raw is None:
        return None, None
    parsed = parse_amount_text(amount_raw) or parse_amount_text(
        amount_raw.replace(" ", "").replace("\u3000", "")
    )
    if parsed is None:
        return amount_raw, None
    return amount_raw, parsed.amount_yuan


def _new_version(
    *,
    rule_id: str,
    version_number: int,
    name: str,
    category: str | None,
    comparator: str | None,
    amount_yuan: int | None,
    amount_raw: str | None,
    source_clause: str | None,
    source_page: int | None,
    source_quote: str | None,
    policy_id: str | None,
) -> RuleVersion:
    snapshot = {
        "name": name,
        "category": category,
        "compare_field": COMPARE_FIELD,
        "comparator": comparator,
        "amount_yuan": amount_yuan,
        "amount_raw": amount_raw,
        "source_clause": source_clause,
        "source_page": source_page,
        "source_quote": source_quote,
        "policy_id": policy_id,
    }
    return RuleVersion(
        id=str(uuid.uuid4()),
        rule_id=rule_id,
        version_number=version_number,
        name=name,
        category=category,
        compare_field=COMPARE_FIELD,
        comparator=comparator,
        amount_yuan=amount_yuan,
        amount_raw=amount_raw,
        source_clause=source_clause,
        source_page=source_page,
        source_quote=source_quote,
        policy_id=policy_id,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )
