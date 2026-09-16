"""RULE-004 / RULE-010: project period window and commitment signing date.

Program owns the calendar comparison. Extraction may return unresolved dates;
missing or unreliable values become NEED_HUMAN_REVIEW, never invented dates.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Material, MaterialCategory, MaterialStatus
from app.review_contract import (
    ReviewCheckStatus,
    ReviewEvidence,
    ReviewEvidenceBBox,
    RuleExecutionContext,
    RuleExecutionResult,
)
from app.services.date_extract import (
    DateBBox,
    ExtractedDate,
    ExtractedPeriod,
    extract_execution_period_from_pdf,
    extract_signing_date_from_pdf,
)
from app.services.storage import material_file_path

RULE_004 = "RULE-004"
RULE_010 = "RULE-010"

PERIOD_WINDOW_START = date(2027, 1, 1)
PERIOD_WINDOW_END = date(2028, 12, 31)
PERIOD_MAX_MONTHS = 24
SIGNING_DEADLINE = date(2026, 9, 30)


def execute_date_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    """W3 executor entry: Session + context in, RuleExecutionResult out."""
    try:
        if context.rule_code == RULE_004:
            return _execute_period_rule(db, context)
        if context.rule_code == RULE_010:
            return _execute_signing_rule(db, context)
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"日期规则执行器不支持 {context.rule_code}",
            data={"rule_id": context.rule_code},
        )
    except Exception as exc:  # noqa: BLE001 — surface as SYSTEM_ERROR
        return RuleExecutionResult(
            status=ReviewCheckStatus.SYSTEM_ERROR,
            summary=f"日期规则执行失败：{exc}",
            data={"rule_id": context.rule_code},
        )


def evaluate_period_rule(
    period: ExtractedPeriod,
    *,
    material: Material | None = None,
) -> RuleExecutionResult:
    """Compare extracted start/end dates with the batch window and 24-month cap."""
    evidence = [
        _evidence_from_date(period.start, material),
        _evidence_from_date(period.end, material),
    ]
    start = period.start
    end = period.end
    base_data: dict[str, object] = {
        "rule_id": RULE_004,
        "start_date": start.iso_value,
        "end_date": end.iso_value,
        "raw_period": period.raw_value,
        "window_start": PERIOD_WINDOW_START.isoformat(),
        "window_end": PERIOD_WINDOW_END.isoformat(),
        "max_months": PERIOD_MAX_MONTHS,
        "start_ok": None,
        "end_ok": None,
        "duration_ok": None,
        "violations": [],
    }

    if not start.reliable or start.parsed is None or not end.reliable or end.parsed is None:
        reason = start.reason or end.reason or "执行期日期无法可靠确定，需人工确认"
        raw = period.raw_value or start.raw_value or end.raw_value
        summary = (
            f"执行期原文「{raw}」无法确定起止日期，需人工确认。"
            if raw
            else reason
        )
        return RuleExecutionResult(
            status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
            summary=summary,
            evidence=evidence,
            data=base_data,
        )

    start_day = start.parsed
    end_day = end.parsed
    start_ok = start_day >= PERIOD_WINDOW_START
    end_ok = end_day <= PERIOD_WINDOW_END
    duration_ok = period_within_max_months(start_day, end_day)
    violations: list[str] = []
    messages: list[str] = []
    if end_day < start_day:
        violations.append("end_before_start")
        messages.append(f"结束日期 {end_day.isoformat()} 早于开始日期 {start_day.isoformat()}")
    if not start_ok:
        violations.append("start_before_window")
        messages.append(
            f"开始日期 {start_day.isoformat()} 早于允许窗口 {PERIOD_WINDOW_START.isoformat()}"
        )
    if not end_ok:
        violations.append("end_after_window")
        messages.append(
            f"结束日期 {end_day.isoformat()} 晚于允许窗口 {PERIOD_WINDOW_END.isoformat()}"
        )
    if not duration_ok and end_day >= start_day:
        violations.append("duration_over_max")
        messages.append(
            f"执行期 {start_day.isoformat()} 至 {end_day.isoformat()} "
            f"超过最长 {PERIOD_MAX_MONTHS} 个月"
        )

    base_data["start_ok"] = start_ok
    base_data["end_ok"] = end_ok
    base_data["duration_ok"] = duration_ok
    base_data["violations"] = violations

    if violations:
        return RuleExecutionResult(
            status=ReviewCheckStatus.FAIL,
            summary="；".join(messages) + "。",
            evidence=evidence,
            data=base_data,
        )

    return RuleExecutionResult(
        status=ReviewCheckStatus.PASS,
        summary=(
            f"执行期 {start_day.isoformat()} 至 {end_day.isoformat()}，"
            f"落在 {PERIOD_WINDOW_START.isoformat()}～{PERIOD_WINDOW_END.isoformat()} "
            f"窗口内且周期不超过 {PERIOD_MAX_MONTHS} 个月。"
        ),
        evidence=evidence,
        data=base_data,
    )


def evaluate_signing_rule(
    extracted: ExtractedDate,
    *,
    material: Material | None = None,
) -> RuleExecutionResult:
    """Compare extracted signing date with the batch deadline."""
    evidence = [_evidence_from_date(extracted, material)]
    base_data: dict[str, object] = {
        "rule_id": RULE_010,
        "signing_date": extracted.iso_value,
        "raw_signing": extracted.raw_value,
        "deadline": SIGNING_DEADLINE.isoformat(),
        "deadline_ok": None,
    }
    if not extracted.reliable or extracted.parsed is None:
        raw = extracted.raw_value
        reason = extracted.reason or "承诺书签署日期无法可靠确定，需人工确认"
        summary = (
            f"签署日期原文「{raw}」不完整，无法确定是否晚于截止日，需人工确认。"
            if raw
            else reason
        )
        return RuleExecutionResult(
            status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
            summary=summary,
            evidence=evidence,
            data=base_data,
        )

    deadline_ok = extracted.parsed <= SIGNING_DEADLINE
    base_data["deadline_ok"] = deadline_ok
    if not deadline_ok:
        return RuleExecutionResult(
            status=ReviewCheckStatus.FAIL,
            summary=(
                f"承诺书签署日期 {extracted.iso_value} 晚于截止日 "
                f"{SIGNING_DEADLINE.isoformat()}。"
            ),
            evidence=evidence,
            data=base_data,
        )
    return RuleExecutionResult(
        status=ReviewCheckStatus.PASS,
        summary=(
            f"承诺书签署日期 {extracted.iso_value}，不晚于截止日 "
            f"{SIGNING_DEADLINE.isoformat()}。"
        ),
        evidence=evidence,
        data=base_data,
    )


def period_within_max_months(
    start: date,
    end: date,
    max_months: int = PERIOD_MAX_MONTHS,
) -> bool:
    """True when end is on or before the last day of a max_months calendar span.

    2027-01-01 to 2028-12-31 is exactly 24 months and is allowed.
    """
    if end < start:
        return False
    return end < _add_months(start, max_months)


def _execute_period_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    material = _latest_ready_material(db, context.project_id, MaterialCategory.APPLICATION)
    if material is None:
        return RuleExecutionResult(
            status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
            summary="缺少就绪的申报书，无法确定执行期起止日期，需人工确认。",
            data={
                "rule_id": RULE_004,
                "start_date": None,
                "end_date": None,
                "raw_period": None,
                "window_start": PERIOD_WINDOW_START.isoformat(),
                "window_end": PERIOD_WINDOW_END.isoformat(),
                "max_months": PERIOD_MAX_MONTHS,
                "start_ok": None,
                "end_ok": None,
                "duration_ok": None,
                "violations": [],
            },
        )
    path = _require_material_path(material)
    period = extract_execution_period_from_pdf(path)
    return evaluate_period_rule(period, material=material)


def _execute_signing_rule(db: Session, context: RuleExecutionContext) -> RuleExecutionResult:
    material = _latest_ready_material(db, context.project_id, MaterialCategory.COMMITMENT)
    if material is None:
        return RuleExecutionResult(
            status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
            summary="缺少就绪的承诺书，无法确定签署日期，需人工确认。",
            data={
                "rule_id": RULE_010,
                "signing_date": None,
                "raw_signing": None,
                "deadline": SIGNING_DEADLINE.isoformat(),
                "deadline_ok": None,
            },
        )
    path = _require_material_path(material)
    extracted = extract_signing_date_from_pdf(path)
    return evaluate_signing_rule(extracted, material=material)


def _require_material_path(material: Material, settings: Settings | None = None) -> Path:
    path = material_file_path(material.id, settings or get_settings())
    if not path.is_file():
        raise FileNotFoundError(f"材料文件不存在：{material.original_filename}")
    return path


def _latest_ready_material(
    db: Session,
    project_id: str,
    category: MaterialCategory,
) -> Material | None:
    statement = (
        select(Material)
        .where(
            Material.project_id == project_id,
            Material.category == category.value,
            Material.status == MaterialStatus.READY.value,
        )
        .order_by(Material.created_at.desc())
        .limit(1)
    )
    return db.scalars(statement).first()


def _evidence_from_date(extracted: ExtractedDate, material: Material | None) -> ReviewEvidence:
    return ReviewEvidence(
        material_id=None if material is None else material.id,
        category=None if material is None else material.category,
        original_filename=None if material is None else material.original_filename,
        field_name=extracted.field_name,
        raw_value=extracted.raw_value,
        normalized_value=extracted.iso_value,
        page_number=extracted.page_number,
        quote=extracted.quote,
        bbox=_to_evidence_bbox(extracted.bbox),
        reliable=extracted.reliable,
        reason=extracted.reason,
    )


def _to_evidence_bbox(bbox: DateBBox | None) -> ReviewEvidenceBBox | None:
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


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(value.day, last_day))
