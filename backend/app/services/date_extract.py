"""Extract project-period and commitment signing dates from PDF materials.

Primary path: native PDF text around labelled fields (deterministic).
Incomplete, vague, or unreadable dates stay unresolved — never invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

import pymupdf

from app.services import pdf as pdf_service

PERIOD_LABELS = ("项目执行期", "执行期", "起止日期", "项目周期")
START_LABELS = ("开始日期", "起始日期", "项目开始日期")
END_LABELS = ("结束日期", "终止日期", "项目结束日期")
SIGNING_LABELS = ("项目负责人签署日期", "签署日期")

_OTHER_FIELD_LABELS = frozenset(
    {
        *PERIOD_LABELS,
        *START_LABELS,
        *END_LABELS,
        *SIGNING_LABELS,
        "申请经费",
        "申请总额",
        "申请金额",
        "项目负责人",
        "项目类别",
        "所在单位",
        "研究方向",
        "项目名称",
    }
)

# Zero-padded ISO used by the development-batch PDFs.
_ISO_PADDED = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
# 2027/1/1 or 2027.01.01 — not hyphenated YYYY-MM-D (that form is treated as incomplete).
_ISO_SLASH_DOT = re.compile(r"(?<!\d)(\d{4})([/.])(\d{1,2})\2(\d{1,2})(?!\d)")
_ISO_UNPADDED_MONTH = re.compile(r"(?<!\d)(\d{4})-(\d{1})-(\d{1,2})(?!\d)")
# Padded month + single-digit day: "2026-09-2" after stamp occlusion.
_ISO_INCOMPLETE_DAY = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d)(?!\d)")
_CN_COMPLETE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CN_PARTIAL = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")
_INCOMPLETE_MARK = re.compile(
    r"\d{4}[-/.年]\s*\d{1,2}[-/.月]?\s*\d{0,2}\s*[?？]"
)
_VAGUE_PERIOD = re.compile(
    r"约\s*[一二两三四五六七八九十\d]+\s*年"
    r"|两年左右"
    r"|一年左右"
    r"|立项后启动"
    r"|待定"
)


@dataclass(frozen=True, slots=True)
class DateBBox:
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


@dataclass(frozen=True, slots=True)
class ExtractedDate:
    """One labelled date, or an unresolved attempt to read it."""

    field_name: str
    raw_value: str | None
    iso_value: str | None
    parsed: date | None
    page_number: int | None
    quote: str | None
    bbox: DateBBox | None
    reliable: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class ExtractedPeriod:
    start: ExtractedDate
    end: ExtractedDate
    raw_value: str | None
    page_number: int | None
    quote: str | None


def parse_date_text(text: str, *, field_name: str = "签署日期") -> ExtractedDate:
    """Parse a single date field value (no PDF). Incomplete tokens stay unreliable."""
    return _date_from_field(text, field_name=field_name)


def parse_period_text(text: str) -> ExtractedPeriod:
    """Parse an execution-period field value (no PDF)."""
    return _period_from_field(text)


def extract_execution_period_from_pdf(path: Path) -> ExtractedPeriod:
    """Read 执行期 / start+end dates from a PDF (typically APPLICATION)."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        unresolved = _unresolved_date(
            "执行期",
            reason=f"无法读取 PDF：{exc.message}",
        )
        return ExtractedPeriod(
            start=unresolved,
            end=unresolved,
            raw_value=None,
            page_number=None,
            quote=None,
        )
    try:
        return _extract_period_from_doc(doc)
    finally:
        doc.close()


def extract_signing_date_from_pdf(path: Path) -> ExtractedDate:
    """Read 签署日期 from a PDF (typically COMMITMENT)."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return _unresolved_date(
            "签署日期",
            reason=f"无法读取 PDF：{exc.message}",
        )
    try:
        return _extract_signing_from_doc(doc)
    finally:
        doc.close()


def _extract_period_from_doc(doc: pymupdf.Document) -> ExtractedPeriod:
    start_hit: tuple[str, int, pymupdf.Page] | None = None
    end_hit: tuple[str, int, pymupdf.Page] | None = None

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        text = page.get_text() or ""

        for label in PERIOD_LABELS:
            field = _field_after_label(text, label)
            if field is None:
                continue
            return _period_from_field(
                field,
                page=page,
                page_number=page_number,
                label=label,
            )

        if start_hit is None:
            for label in START_LABELS:
                field = _field_after_label(text, label)
                if field is not None:
                    start_hit = (field, page_number, page)
                    break
        if end_hit is None:
            for label in END_LABELS:
                field = _field_after_label(text, label)
                if field is not None:
                    end_hit = (field, page_number, page)
                    break

    if start_hit is not None or end_hit is not None:
        start_field, start_page_no, start_page = start_hit or ("", None, None)
        end_field, end_page_no, end_page = end_hit or ("", None, None)
        start = (
            _date_from_field(
                start_field,
                field_name="开始日期",
                page=start_page,
                page_number=start_page_no,
                label="开始日期",
            )
            if start_hit
            else _unresolved_date("开始日期", reason="未找到开始日期")
        )
        end = (
            _date_from_field(
                end_field,
                field_name="结束日期",
                page=end_page,
                page_number=end_page_no,
                label="结束日期",
            )
            if end_hit
            else _unresolved_date("结束日期", reason="未找到结束日期")
        )
        raw = " ".join(part for part in (start_field, end_field) if part).strip() or None
        return ExtractedPeriod(
            start=start,
            end=end,
            raw_value=raw,
            page_number=start.page_number or end.page_number,
            quote=raw,
        )

    return ExtractedPeriod(
        start=_unresolved_date("开始日期", reason="未能在材料中可靠读取执行期起止日期"),
        end=_unresolved_date("结束日期", reason="未能在材料中可靠读取执行期起止日期"),
        raw_value=None,
        page_number=None,
        quote=None,
    )


def _extract_signing_from_doc(doc: pymupdf.Document) -> ExtractedDate:
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        text = page.get_text() or ""
        for label in SIGNING_LABELS:
            field = _field_after_label(text, label)
            if field is None:
                continue
            return _date_from_field(
                field,
                field_name="签署日期",
                page=page,
                page_number=page_number,
                label=label,
            )
    return _unresolved_date("签署日期", reason="未能在承诺书中可靠读取签署日期")


def _period_from_field(
    field: str,
    *,
    page: pymupdf.Page | None = None,
    page_number: int | None = None,
    label: str = "执行期",
) -> ExtractedPeriod:
    complete = _complete_dates(field)
    if len(complete) >= 2:
        start_date, start_raw = complete[0]
        end_date, end_raw = complete[1]
        quote = f"{label} {field}".strip()
        start = _resolved_date(
            "开始日期",
            raw_value=start_raw,
            parsed=start_date,
            page_number=page_number,
            quote=quote,
            bbox=_locate_bbox(page, (start_raw, start_date.isoformat()), label),
        )
        end = _resolved_date(
            "结束日期",
            raw_value=end_raw,
            parsed=end_date,
            page_number=page_number,
            quote=quote,
            bbox=_locate_bbox(page, (end_raw, end_date.isoformat()), label),
        )
        return ExtractedPeriod(
            start=start,
            end=end,
            raw_value=field.strip(),
            page_number=page_number,
            quote=quote,
        )

    if _is_vague_period(field):
        reason = "执行期仅有约数年等描述，无法确定起止日期"
    elif _has_incomplete_date(field) or len(complete) == 1:
        reason = "执行期日期不完整，无法确定窗口和精确时长"
    elif field.strip():
        reason = "未能从执行期字段解析出确定的开始与结束日期"
    else:
        reason = "执行期字段为空，无法确定起止日期"

    quote = f"{label} {field}".strip()
    unresolved = _unresolved_date(
        "执行期",
        raw_value=field.strip() or None,
        page_number=page_number,
        quote=quote,
        reason=reason,
        bbox=_locate_bbox(page, (field.strip(),), label),
    )
    start = ExtractedDate(
        field_name="开始日期",
        raw_value=unresolved.raw_value,
        iso_value=None,
        parsed=None,
        page_number=page_number,
        quote=quote,
        bbox=unresolved.bbox,
        reliable=False,
        reason=reason,
    )
    end = ExtractedDate(
        field_name="结束日期",
        raw_value=unresolved.raw_value,
        iso_value=None,
        parsed=None,
        page_number=page_number,
        quote=quote,
        bbox=unresolved.bbox,
        reliable=False,
        reason=reason,
    )
    return ExtractedPeriod(
        start=start,
        end=end,
        raw_value=field.strip() or None,
        page_number=page_number,
        quote=quote,
    )


def _date_from_field(
    field: str,
    *,
    field_name: str,
    page: pymupdf.Page | None = None,
    page_number: int | None = None,
    label: str | None = None,
) -> ExtractedDate:
    quote = f"{label} {field}".strip() if label else field.strip()
    complete = _complete_dates(field)
    unique_dates = {parsed for parsed, _raw in complete}
    if len(unique_dates) == 1:
        parsed, raw = complete[0]
        needles = (raw, parsed.isoformat(), field.strip())
        return _resolved_date(
            field_name,
            raw_value=raw,
            parsed=parsed,
            page_number=page_number,
            quote=quote,
            bbox=_locate_bbox(page, needles, label or field_name),
        )
    if len(unique_dates) > 1:
        reason = f"{field_name}出现多个不一致的候选值，需人工确认"
    elif _has_incomplete_date(field):
        reason = f"{field_name}不完整，无法可靠确定"
    elif field.strip():
        reason = f"{field_name}无法解析为确定日期，需人工确认"
    else:
        reason = f"未找到{field_name}"
    return _unresolved_date(
        field_name,
        raw_value=field.strip() or None,
        page_number=page_number,
        quote=quote or None,
        reason=reason,
        bbox=_locate_bbox(page, (field.strip(),), label or field_name),
    )


def _complete_dates(text: str) -> list[tuple[date, str]]:
    """Return complete calendar dates in left-to-right order."""
    hits: list[tuple[int, int, date, str]] = []

    def _add(match: re.Match[str], year: str, month: str, day: str) -> None:
        parsed = _to_date(year, month, day)
        if parsed is None:
            return
        hits.append((match.start(), match.end(), parsed, match.group(0)))

    for match in _CN_COMPLETE.finditer(text):
        _add(match, match.group(1), match.group(2), match.group(3))
    for match in _ISO_PADDED.finditer(text):
        _add(match, match.group(1), match.group(2), match.group(3))
    for match in _ISO_SLASH_DOT.finditer(text):
        _add(match, match.group(1), match.group(3), match.group(4))
    for match in _ISO_UNPADDED_MONTH.finditer(text):
        _add(match, match.group(1), match.group(2), match.group(3))

    hits.sort(key=lambda item: (item[0], item[1]))
    deduped: list[tuple[date, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, parsed, raw in hits:
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        deduped.append((parsed, raw))
    return deduped


def _has_incomplete_date(text: str) -> bool:
    if _INCOMPLETE_MARK.search(text):
        return True
    if _ISO_INCOMPLETE_DAY.search(text):
        return True
    if _CN_PARTIAL.search(text) and not _CN_COMPLETE.search(text):
        return True
    return False


def _is_vague_period(text: str) -> bool:
    return _VAGUE_PERIOD.search(text) is not None


def _to_date(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _field_after_label(text: str, label: str) -> str | None:
    """Value on the same line as label, or the next non-empty content line."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if label not in line:
            continue
        after = line.split(label, 1)[1]
        after = after.lstrip(" :：\t|")
        if after.strip():
            return after.strip()
        for nxt in lines[index + 1 : index + 4]:
            candidate = nxt.strip()
            if not candidate:
                continue
            if _looks_like_new_label(candidate):
                break
            return candidate
    return None


def _looks_like_new_label(line: str) -> bool:
    stripped = line.strip().rstrip(" :：")
    if stripped in _OTHER_FIELD_LABELS:
        return True
    if stripped.startswith("一、") or stripped.startswith("二、"):
        return True
    return False


def _locate_bbox(
    page: pymupdf.Page | None,
    needles: Sequence[str],
    label: str,
) -> DateBBox | None:
    if page is None:
        return None
    rect = None
    for needle in needles:
        cleaned = needle.strip()
        if not cleaned:
            continue
        hits = page.search_for(cleaned)
        if hits:
            rect = hits[0]
            break
    if rect is None:
        hits = page.search_for(label)
        if hits:
            rect = hits[0]
    if rect is None:
        return None
    return DateBBox(
        x0=float(rect.x0),
        y0=float(rect.y0),
        x1=float(rect.x1),
        y1=float(rect.y1),
        page_width=float(page.rect.width),
        page_height=float(page.rect.height),
    )


def _resolved_date(
    field_name: str,
    *,
    raw_value: str,
    parsed: date,
    page_number: int | None,
    quote: str | None,
    bbox: DateBBox | None,
) -> ExtractedDate:
    return ExtractedDate(
        field_name=field_name,
        raw_value=raw_value,
        iso_value=parsed.isoformat(),
        parsed=parsed,
        page_number=page_number,
        quote=quote,
        bbox=bbox,
        reliable=True,
        reason=None,
    )


def _unresolved_date(
    field_name: str,
    *,
    raw_value: str | None = None,
    page_number: int | None = None,
    quote: str | None = None,
    reason: str,
    bbox: DateBBox | None = None,
) -> ExtractedDate:
    return ExtractedDate(
        field_name=field_name,
        raw_value=raw_value,
        iso_value=None,
        parsed=None,
        page_number=page_number,
        quote=quote or raw_value,
        bbox=bbox,
        reliable=False,
        reason=reason,
    )
