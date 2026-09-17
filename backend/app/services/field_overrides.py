"""Apply human field corrections onto extracted values before rule comparison."""

from __future__ import annotations

from dataclasses import replace

from app.review_contract import FieldOverride
from app.services.funding_extract import ExtractedFunding
from app.services.identity_extract import (
    ExtractedIdentity,
    normalize_person_name,
    normalize_project_name,
)
from app.services.money import parse_amount_text

AMOUNT_FIELDS = frozenset({"申请经费", "申请总额", "申请金额"})
PRINCIPAL_FIELDS = frozenset({"项目负责人"})
NAME_FIELDS = frozenset({"项目名称"})
HUMAN_REASON = "人工修正"


def canonical_field(name: str) -> str:
    cleaned = (name or "").strip()
    if cleaned in AMOUNT_FIELDS:
        return "application_amount"
    if cleaned in PRINCIPAL_FIELDS:
        return "principal"
    if cleaned in NAME_FIELDS:
        return "project_name"
    return cleaned


def affected_rule_codes(field_name: str, category: str | None) -> list[str]:
    """Rules that must be re-executed after this extracted field changes."""
    key = canonical_field(field_name)
    if key == "principal":
        return ["RULE-003"]
    if key == "project_name":
        return ["RULE-002"]
    if key == "application_amount":
        codes = ["RULE-007", "RULE-005"]
        if category == "BUDGET" or field_name.strip() == "申请总额":
            codes.append("RULE-006")
        return codes
    return []


def find_override(
    overrides: list[FieldOverride] | None,
    material_id: str | None,
    field_name: str,
) -> FieldOverride | None:
    if not overrides or not material_id:
        return None
    wanted = canonical_field(field_name)
    matches = [
        item
        for item in overrides
        if item.material_id == material_id and canonical_field(item.field_name) == wanted
    ]
    return matches[-1] if matches else None


def apply_identity_override(
    extracted: ExtractedIdentity,
    material_id: str | None,
    overrides: list[FieldOverride] | None,
    field_name: str,
) -> ExtractedIdentity:
    override = find_override(overrides, material_id, field_name)
    if override is None:
        return extracted
    normalizer = normalize_person_name if extracted.field_kind == "principal" else normalize_project_name
    return replace(
        extracted,
        raw_value=override.raw_value,
        normalized_value=normalizer(override.raw_value),
        reliable=True,
        reason=HUMAN_REASON,
    )


def apply_funding_override(
    extracted: ExtractedFunding,
    material_id: str | None,
    overrides: list[FieldOverride] | None,
    field_name: str,
) -> ExtractedFunding:
    override = find_override(overrides, material_id, field_name)
    if override is None:
        return extracted
    parsed = parse_amount_text(override.raw_value, default_unit="yuan")
    if parsed is None:
        return replace(
            extracted,
            raw_value=override.raw_value,
            amount_yuan=None,
            reliable=False,
            reason="人工修正的金额无法解析，不能当作 0",
        )
    return replace(
        extracted,
        field_kind="application_funding",
        raw_value=override.raw_value,
        raw_unit=parsed.raw_unit or extracted.raw_unit,
        amount_yuan=parsed.amount_yuan,
        reliable=True,
        reason=HUMAN_REASON,
    )
