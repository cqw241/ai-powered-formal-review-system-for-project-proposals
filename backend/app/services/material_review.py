"""RULE-001 / RULE-008 / RULE-009 executors.

Integration calls ``execute_material_rule(db, context)``. This module does not
write review_items; it only returns RuleExecutionResult. Missing files are FAIL
only after the package is frozen. Upload-in-progress stays NEED_HUMAN_REVIEW.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Material, MaterialCategory, MaterialStatus
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    ReviewEvidenceBBox,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.services.funding_extract import BBox
from app.services.material_extract import (
    CATEGORY_LABELS,
    EQUIPMENT_THRESHOLD_YUAN,
    REQUIRED_CATEGORIES,
    AttachmentHit,
    EquipmentItem,
    EthicsSignal,
    ExtractedEquipment,
    MaterialManifest,
    collect_attachment_absence,
    extract_equipment,
    extract_ethics_signals,
    find_equipment_attachment,
    load_manifest,
)
from app.services.storage import material_file_path

RULE_001 = "RULE-001"
RULE_008 = "RULE-008"
RULE_009 = "RULE-009"


def execute_material_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """W4 RuleExecutor entry for B10."""
    try:
        if context.rule_code == RULE_001:
            return execute_required_materials_rule(db, context)
        if context.rule_code == RULE_008:
            return execute_equipment_attachment_rule(db, context)
        if context.rule_code == RULE_009:
            return execute_ethics_rule(db, context)
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"材料规则执行器不支持 {context.rule_code}",
            data={"rule_code": context.rule_code},
        )
    except Exception as exc:  # noqa: BLE001 — surface as SYSTEM_ERROR, never as material FAIL
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"材料规则执行失败：{exc}",
            data={"rule_code": context.rule_code, "error": str(exc)},
        )


def execute_required_materials_rule(
    db: Session,
    context: RuleExecutionContext,
) -> RuleExecutionResult:
    """RULE-001: 申报书 / 经费预算表 / 承诺书 each present and readable."""
    manifest = load_manifest(db, context.project_id)
    evidence = _manifest_evidence(manifest)
    data: dict[str, Any] = {
        "rule_code": RULE_001,
        "frozen": manifest.frozen,
        "processing_count": len(manifest.processing),
        "failed_count": len(manifest.failed),
        "present_categories": sorted({item.category for item in manifest.ready}),
    }

    incomplete = _incomplete_package_result(manifest, evidence, data)
    if incomplete is not None:
        return incomplete

    missing_labels: list[str] = []
    unreadable_labels: list[str] = []
    for category in REQUIRED_CATEGORIES:
        label = CATEGORY_LABELS[category.value]
        ready = manifest.ready_in(category)
        failed = [item for item in manifest.all_in(category) if item.status == MaterialStatus.FAILED.value]
        if ready:
            continue
        if failed:
            unreadable_labels.append(label)
            continue
        missing_labels.append(label)

    data["missing"] = missing_labels
    data["unreadable"] = unreadable_labels

    if unreadable_labels:
        joined = "、".join(unreadable_labels)
        return _human(
            f"{joined}无法读取，不能把尚未找到当成确认缺件；待确认：文件损坏",
            evidence=evidence,
            data=data,
            fields=unreadable_labels,
        )
    if missing_labels:
        joined = "、".join(missing_labels)
        return RuleExecutionResult(
            status=ReviewCheckStatus.FAIL,
            summary=f"完整提交范围内缺少{joined}，确认为缺件",
            evidence=evidence,
            data=data,
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus.PASS,
        summary="申报书、经费预算表、科研诚信与合规承诺书均已提交且可读",
        evidence=evidence,
        data=data,
    )


def execute_equipment_attachment_rule(
    db: Session,
    context: RuleExecutionContext,
) -> RuleExecutionResult:
    """RULE-008: 单台/套 ≥ 5 万元时必须有设备必要性说明."""
    manifest = load_manifest(db, context.project_id)
    settings = get_settings()
    evidence: list[ReviewEvidence] = []
    data: dict[str, Any] = {
        "rule_code": RULE_008,
        "threshold_yuan": EQUIPMENT_THRESHOLD_YUAN,
        "frozen": manifest.frozen,
    }

    incomplete = _incomplete_package_result(manifest, evidence, data)
    if incomplete is not None:
        return incomplete

    budget_materials = manifest.ready_in(MaterialCategory.BUDGET)
    if not budget_materials:
        return _human(
            "缺少就绪的经费预算表，无法判断是否触发大额设备附件；待确认字段：设备单价",
            evidence=evidence,
            data=data,
            fields=["设备单价"],
        )

    extracted = extract_equipment(material_file_path(budget_materials[-1].id, settings))
    evidence.extend(_equipment_evidence(budget_materials[-1], extracted))
    data.update(extracted.as_dict())

    triggered_items = [
        item
        for item in extracted.items
        if item.unit_price_known
        and item.unit_price_yuan is not None
        and item.unit_price_yuan >= EQUIPMENT_THRESHOLD_YUAN
    ]
    data["triggered_unit_prices"] = [item.unit_price_yuan for item in triggered_items]

    if extracted.unit_price_uncertain and not triggered_items:
        return _human(
            (extracted.uncertain_reason or "设备只有总价、数量/单价不可确定")
            + "；待确认字段：设备单价",
            evidence=evidence,
            data=data,
            fields=["设备单价"],
        )

    applicable = bool(triggered_items)
    if not applicable and extracted.max_unit_price_yuan is not None:
        applicable = extracted.max_unit_price_yuan >= EQUIPMENT_THRESHOLD_YUAN
    if (
        not applicable
        and not extracted.items
        and extracted.equipment_fee_yuan is not None
        and extracted.equipment_fee_yuan >= EQUIPMENT_THRESHOLD_YUAN
    ):
        return _human(
            "设备费已达到或超过5万元，但没有单台/套单价，无法判断是否触发附件义务；待确认字段：设备单价",
            evidence=evidence,
            data=data,
            fields=["设备单价"],
        )

    if not applicable:
        summary = (
            "所有单台/套设备均低于5万元，大额设备必要性说明不适用"
            if extracted.max_unit_price_yuan is not None
            else "未触发大额设备附件义务，本条不适用"
        )
        if extracted.no_large_device_stated:
            summary = "预算载明无单台/套达到5万元设备，大额设备必要性说明不适用"
        return RuleExecutionResult(
            status=ReviewCheckStatus.NOT_APPLICABLE,
            summary=summary,
            evidence=evidence,
            data=data,
        )

    attachment = find_equipment_attachment(db, manifest, settings)
    data["attachment_present"] = attachment is not None
    if attachment is not None:
        evidence.append(_attachment_evidence(attachment))
        prices = "、".join(str(item.unit_price_yuan) for item in triggered_items)
        return RuleExecutionResult(
            status=ReviewCheckStatus.PASS,
            summary=f"设备单台/套达到5万元（{prices} 元），已提供设备必要性说明",
            evidence=evidence,
            data=data,
        )

    if manifest.failed:
        evidence.extend(_failed_material_evidence(manifest.failed))
        return _human(
            "存在无法读取的材料，不能把尚未找到当成确认缺附件；待确认：文件损坏",
            evidence=evidence,
            data=data,
            fields=["设备必要性说明"],
        )

    if extracted.absence_stated and extracted.absence_quote:
        evidence.append(
            ReviewEvidence(
                material_id=budget_materials[-1].id,
                category=budget_materials[-1].category,
                original_filename=budget_materials[-1].original_filename,
                field_name="设备必要性说明",
                raw_value=extracted.absence_quote,
                normalized_value=False,
                page_number=extracted.page_number,
                quote=extracted.absence_quote,
                reliable=True,
                reason="完整范围内确认未提供设备必要性说明",
            )
        )
    evidence.extend(_absence_quotes_from_pack(manifest, settings))

    price_text = "、".join(
        f"{item.name}{item.unit_price_yuan}元" for item in triggered_items if item.unit_price_yuan is not None
    )
    return RuleExecutionResult(
        status=ReviewCheckStatus.FAIL,
        summary=f"{price_text}已触发必要性说明，完整范围内确认缺少该附件",
        evidence=evidence,
        data=data,
    )


def execute_ethics_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """RULE-009: 数据/伦理适用性。不清时列出依据和待确认问题，不直接通过。"""
    manifest = load_manifest(db, context.project_id)
    settings = get_settings()
    evidence: list[ReviewEvidence] = []
    data: dict[str, Any] = {"rule_code": RULE_009, "frozen": manifest.frozen}

    incomplete = _incomplete_package_result(manifest, evidence, data)
    if incomplete is not None:
        return incomplete

    if not manifest.ready:
        return _human(
            "没有可读材料，无法判断数据与伦理适用性；待确认字段：伦理适用性",
            evidence=evidence,
            data=data,
            fields=["伦理适用性"],
        )

    signals: list[EthicsSignal] = []
    for material in manifest.ready:
        if material.category not in {
            MaterialCategory.APPLICATION.value,
            MaterialCategory.COMMITMENT.value,
            MaterialCategory.OTHER.value,
        }:
            continue
        path = material_file_path(material.id, settings)
        signals.extend(extract_ethics_signals(material, path))

    evidence.extend(_ethics_evidence(signals))
    kinds = {item.kind for item in signals if item.reliable}
    data["signal_kinds"] = sorted(kinds)
    data["quotes"] = [item.quote for item in signals if item.quote]

    involved = "involved" in kinds
    not_involved = "not_involved" in kinds
    unclear = "unclear" in kinds
    has_approval = "approval" in kinds
    missing_proof = "missing_proof" in kinds
    submission_required = "submission_required" in kinds
    data.update(
        {
            "involved": involved,
            "not_involved": not_involved,
            "unclear": unclear,
            "has_approval": has_approval,
            "missing_proof": missing_proof,
            "submission_required": submission_required,
        }
    )

    explicit_missing = any(
        item.kind == "missing_proof" and "既无审批编号" in (item.quote or "")
        for item in signals
    )
    confirmed_missing = involved and submission_required and (
        explicit_missing or (missing_proof and not has_approval)
    )
    if confirmed_missing:
        if manifest.failed:
            evidence.extend(_failed_material_evidence(manifest.failed))
            return _human(
                "存在无法读取的材料，不能确认伦理审批材料缺失；待确认：文件损坏",
                evidence=evidence,
                data=data,
                fields=["伦理审批材料"],
            )
        questions = _ethics_questions(signals)
        data["pending_questions"] = questions
        return RuleExecutionResult(
            status=ReviewCheckStatus.FAIL,
            summary="材料明确涉及人体受试者且提交时须提供伦理审批，完整范围内既无审批编号也无进行中证明",
            evidence=evidence,
            data=data,
        )

    if not_involved and not involved:
        return RuleExecutionResult(
            status=ReviewCheckStatus.PASS,
            summary="材料明确不涉及人体受试者、个人敏感信息、生物样本或受限制数据",
            evidence=evidence,
            data=data,
        )

    if involved and has_approval:
        return RuleExecutionResult(
            status=ReviewCheckStatus.PASS,
            summary="材料涉及触发对象，且已提供伦理/数据合规审批编号或进行中证明（仅形式满足）",
            evidence=evidence,
            data=data,
        )

    questions = _ethics_questions(signals)
    data["pending_questions"] = questions
    if not kinds:
        return _human(
            "材料未给出可判定适用性的明确表述，无法确定是否涉及人体受试者、个人敏感信息、生物样本或受限制数据，不直接通过",
            evidence=evidence,
            data=data,
            fields=["伦理适用性"],
        )
    question_text = "；".join(questions) if questions else "伦理适用性或审批/授权关系不清"
    basis = _basis_text(signals)
    return _human(
        f"伦理适用性不清。依据：{basis}。待确认：{question_text}",
        evidence=evidence,
        data=data,
        fields=["伦理适用性"],
    )


def _incomplete_package_result(
    manifest: MaterialManifest,
    evidence: list[ReviewEvidence],
    data: dict[str, Any],
) -> RuleExecutionResult | None:
    if not manifest.materials:
        return _human(
            "尚未提交材料，上传未完成，不能确认缺件；材料仍在处理中",
            evidence=evidence,
            data=data,
            fields=["材料清单"],
        )
    if manifest.processing:
        names = "、".join(item.original_filename for item in manifest.processing)
        evidence.extend(_processing_evidence(manifest.processing))
        return _human(
            f"上传未完成（{names} 仍在处理中），不能把尚未找到当成确认缺件",
            evidence=evidence,
            data=data,
            fields=["材料清单"],
        )
    return None


def _ethics_questions(signals: list[EthicsSignal]) -> list[str]:
    text = " ".join(item.quote for item in signals)
    questions: list[str] = []
    if any(token in text for token in ("脑电", "合作单位", "去标识化")):
        questions.extend(
            [
                "合作单位去标识化历史脑电数据是否属于人体受试者、生物样本或受限制数据？",
                "提交时是否必须提供伦理审批编号或审批进行中证明？",
                "合作单位数据授权与校内伦理义务关系如何认定？",
            ]
        )
    if any(token in text for token in ("问卷", "学习日志", "课程作业", "个人敏感信息")):
        questions.append("课程问卷/学习数据是否构成个人敏感信息并触发伦理审批？")
    if any(item.kind == "involved" for item in signals):
        questions.append("现有材料中的审批编号或进行中证明是否满足提交时形式要求？")
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for item in questions:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    if not unique:
        unique.append("当前数据是否属于人体受试者、个人敏感信息、生物样本或受限制数据？")
        unique.append("如适用，伦理/数据合规审批编号或进行中证明在何处？")
    return unique


def _basis_text(signals: list[EthicsSignal]) -> str:
    quotes = [item.quote for item in signals if item.quote and item.kind in {"unclear", "involved", "missing_proof"}]
    if not quotes:
        quotes = [item.quote for item in signals if item.quote]
    if not quotes:
        return "材料未给出可判定适用性的明确表述"
    return quotes[0]


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


def _manifest_evidence(manifest: MaterialManifest) -> list[ReviewEvidence]:
    items: list[ReviewEvidence] = []
    seen_categories: set[str] = set()
    for material in manifest.materials:
        label = CATEGORY_LABELS.get(material.category, material.category)
        items.append(
            ReviewEvidence(
                material_id=material.id,
                category=material.category,
                original_filename=material.original_filename,
                field_name="材料类别",
                raw_value=material.original_filename,
                normalized_value=label,
                reliable=material.status == MaterialStatus.READY.value,
                reason=None
                if material.status == MaterialStatus.READY.value
                else f"材料状态为{material.status}，不能当作确认缺件",
            )
        )
        seen_categories.add(material.category)
    for category in REQUIRED_CATEGORIES:
        if category.value in seen_categories:
            continue
        items.append(
            ReviewEvidence(
                category=category.value,
                field_name="材料类别",
                raw_value=None,
                normalized_value=CATEGORY_LABELS[category.value],
                reliable=manifest.frozen,
                reason="完整提交范围内未找到该类别材料" if manifest.frozen else "清单未冻结，不能确认缺失",
            )
        )
    return items


def _processing_evidence(materials: tuple[Material, ...]) -> list[ReviewEvidence]:
    return [
        ReviewEvidence(
            material_id=item.id,
            category=item.category,
            original_filename=item.original_filename,
            field_name="处理状态",
            raw_value=item.status,
            normalized_value=item.status,
            reliable=False,
            reason="上传未完成，材料仍在处理中",
        )
        for item in materials
    ]


def _equipment_evidence(material: Material, extracted: ExtractedEquipment) -> list[ReviewEvidence]:
    items: list[ReviewEvidence] = []
    if extracted.equipment_fee_yuan is not None:
        items.append(
            ReviewEvidence(
                material_id=material.id,
                category=material.category,
                original_filename=material.original_filename,
                field_name="设备费",
                raw_value=str(extracted.equipment_fee_yuan),
                normalized_value=extracted.equipment_fee_yuan,
                page_number=extracted.page_number,
                quote=extracted.equipment_fee_quote,
                reliable=True,
                reason=None,
            )
        )
    for line in extracted.items:
        items.append(_equipment_item_evidence(material, line))
    if extracted.no_large_device_stated:
        items.append(
            ReviewEvidence(
                material_id=material.id,
                category=material.category,
                original_filename=material.original_filename,
                field_name="大额设备适用条件",
                raw_value="不存在单台/套达到5万元设备",
                normalized_value=False,
                reliable=True,
                reason="预算载明未触发大额设备附件义务",
            )
        )
    return items


def _equipment_item_evidence(material: Material, item: EquipmentItem) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=material.id,
        category=material.category,
        original_filename=material.original_filename,
        field_name=item.name,
        raw_value=item.quote,
        normalized_value=item.unit_price_yuan,
        page_number=item.page_number,
        quote=item.quote,
        bbox=_to_bbox(item.bbox),
        reliable=item.unit_price_known,
        reason=item.reason,
    )


def _attachment_evidence(hit: AttachmentHit) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=hit.material_id,
        category=hit.category,
        original_filename=hit.original_filename,
        field_name="设备必要性说明",
        raw_value=hit.quote or hit.original_filename,
        normalized_value=True,
        page_number=hit.page_number,
        quote=hit.quote,
        bbox=_to_bbox(hit.bbox),
        reliable=hit.present,
        reason=hit.reason,
    )


def _failed_material_evidence(materials: tuple[Material, ...]) -> list[ReviewEvidence]:
    return [
        ReviewEvidence(
            material_id=item.id,
            category=item.category,
            original_filename=item.original_filename,
            field_name="处理状态",
            raw_value=item.status,
            normalized_value=item.status,
            reliable=False,
            reason="文件损坏或读取失败，不能当作确认缺失",
        )
        for item in materials
    ]


def _absence_quotes_from_pack(manifest: MaterialManifest, settings) -> list[ReviewEvidence]:
    return [
        ReviewEvidence(
            material_id=hit.material_id,
            category=hit.category,
            original_filename=hit.original_filename,
            field_name="设备必要性说明",
            raw_value=hit.quote,
            normalized_value=False,
            page_number=hit.page_number,
            quote=hit.quote,
            bbox=_to_bbox(hit.bbox),
            reliable=True,
            reason=hit.reason,
        )
        for hit in collect_attachment_absence(manifest, settings)
    ]


def _ethics_evidence(signals: list[EthicsSignal]) -> list[ReviewEvidence]:
    items: list[ReviewEvidence] = []
    seen: set[tuple[str, str, int | None]] = set()
    for signal in signals:
        key = (signal.material_id, signal.kind, signal.page_number)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            ReviewEvidence(
                material_id=signal.material_id,
                category=signal.category,
                original_filename=signal.original_filename,
                field_name=_ethics_field_name(signal.kind),
                raw_value=signal.quote,
                normalized_value=signal.kind,
                page_number=signal.page_number,
                quote=signal.quote,
                bbox=_to_bbox(signal.bbox),
                reliable=signal.reliable,
                reason=signal.reason,
            )
        )
    return items


def _ethics_field_name(kind: str) -> str:
    mapping = {
        "not_involved": "伦理适用性",
        "involved": "伦理适用性",
        "unclear": "伦理适用性",
        "approval": "伦理审批编号",
        "missing_proof": "伦理审批材料",
        "submission_required": "提交时伦理义务",
    }
    return mapping.get(kind, "伦理适用性")
