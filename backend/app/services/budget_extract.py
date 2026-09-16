"""Extract project category and budget-subject lines from PDF materials.

RULE-005 needs a reliable 项目类别. RULE-006 needs subject lines plus 申请总额.
Amount parsing reuses money.py; application-total extraction reuses funding_extract.
Unknown values stay None and are never coerced to 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from app.services import pdf as pdf_service
from app.services.funding_extract import BBox, ExtractedFunding, extract_application_funding_from_pdf
from app.services.money import AmountUnit, parse_amount_text

NATURAL_SCIENCE = "自然科学类"
HUMANITIES = "人文社会科学类"
KNOWN_CATEGORIES = (NATURAL_SCIENCE, HUMANITIES)

CATEGORY_LABEL = "项目类别"

TOTAL_ROW_NAMES = frozenset({"合计", "科目合计", "总计", "小计", "总和", "年度合计"})

SCOPE_UNCERTAIN_MARKERS = (
    "其中：配套经费",
    "其中:配套经费",
    "其中：自筹经费",
    "其中:自筹经费",
    "合并单元格",
    "未标明是否计入",
    "是否应计入",
    "是否计入申请总额",
    "口径不清",
    "无法判断该",
)

MATCHING_SCOPE_NAMES = ("配套经费", "自筹经费")

_HEADER_SKIP_TOKENS = ("数量", "单价", "小计", "测算单价")

_BUDGET_TOTAL_LABELS = ("申请总额", "申请经费", "申请金额")


@dataclass(frozen=True, slots=True)
class ExtractedCategory:
    """Project-category field read from an APPLICATION (or similar) PDF."""

    raw_value: str | None
    normalized: str | None
    page_number: int | None
    quote: str | None
    bbox: BBox | None
    reliable: bool
    reason: str | None
    source: str = "native_text"

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "normalized": self.normalized,
            "page_number": self.page_number,
            "quote": self.quote,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "reliable": self.reliable,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class BudgetLine:
    """One 科目 row from the subject-budget table (or an unresolved attempt)."""

    name: str
    raw_value: str | None
    amount_yuan: int | None
    is_total: bool
    page_number: int | None
    quote: str | None
    bbox: BBox | None
    reliable: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_value": self.raw_value,
            "amount_yuan": self.amount_yuan,
            "is_total": self.is_total,
            "page_number": self.page_number,
            "quote": self.quote,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "reliable": self.reliable,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ExtractedBudget:
    """Budget-table subjects plus the declared 申请总额."""

    application_total: ExtractedFunding
    lines: tuple[BudgetLine, ...]
    line_sum_yuan: int | None
    scope_uncertain: bool
    scope_reason: str | None
    reliable: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_total": self.application_total.as_dict(),
            "lines": [item.as_dict() for item in self.lines],
            "line_sum_yuan": self.line_sum_yuan,
            "scope_uncertain": self.scope_uncertain,
            "scope_reason": self.scope_reason,
            "reliable": self.reliable,
            "reason": self.reason,
        }

    @property
    def detail_lines(self) -> tuple[BudgetLine, ...]:
        return tuple(item for item in self.lines if not item.is_total)


def normalize_category(raw: str | None) -> str | None:
    """Map a short category phrase onto the two policy classes; unknown stays None."""
    if raw is None:
        return None
    text = re.sub(r"\s+", "", raw.strip())
    if not text:
        return None
    if "人文社科" in text or "人文社会科学" in text:
        return HUMANITIES
    if "自然科学" in text:
        return NATURAL_SCIENCE
    return None


def extract_project_category(path: Path) -> ExtractedCategory:
    """Read 项目类别 from a digital PDF. Missing or unknown → unreliable, not a guess."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return ExtractedCategory(
            raw_value=None,
            normalized=None,
            page_number=None,
            quote=None,
            bbox=None,
            reliable=False,
            reason=f"无法读取 PDF：{exc.message}",
            source="none",
        )
    try:
        table_hit = _category_from_tables(doc)
        if table_hit is not None:
            return table_hit
        return _category_from_text(doc)
    finally:
        doc.close()


def extract_budget(path: Path) -> ExtractedBudget:
    """Read 申请总额 and 科目预算 lines. Does not invent zero for missing cells."""
    application_total = extract_application_funding_from_pdf(
        path,
        prefer_labels=_BUDGET_TOTAL_LABELS,
        use_llm_fallback=False,
    )
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return ExtractedBudget(
            application_total=application_total,
            lines=(),
            line_sum_yuan=None,
            scope_uncertain=False,
            scope_reason=None,
            reliable=False,
            reason=f"无法读取预算 PDF：{exc.message}",
        )
    try:
        full_text = "\n".join((page.get_text() or "") for page in doc)
        scope_uncertain, scope_reason = _detect_scope_uncertainty(full_text)
        lines = _lines_from_tables(doc)
        if not lines:
            lines = _lines_from_text(doc)
        details = [item for item in lines if not item.is_total]
        unreliable = [item for item in details if not item.reliable or item.amount_yuan is None]
        if not details:
            return ExtractedBudget(
                application_total=application_total,
                lines=tuple(lines),
                line_sum_yuan=None,
                scope_uncertain=scope_uncertain,
                scope_reason=scope_reason,
                reliable=False,
                reason="未能在预算表中可靠读取科目明细，表格可能不可读或科目缺失",
            )
        if unreliable:
            sample = unreliable[0]
            return ExtractedBudget(
                application_total=application_total,
                lines=tuple(lines),
                line_sum_yuan=None,
                scope_uncertain=scope_uncertain,
                scope_reason=scope_reason,
                reliable=False,
                reason=sample.reason or f"科目「{sample.name}」金额无法解析，不能当作 0",
            )
        line_sum = sum(item.amount_yuan or 0 for item in details)
        reason = scope_reason if scope_uncertain else None
        return ExtractedBudget(
            application_total=application_total,
            lines=tuple(lines),
            line_sum_yuan=line_sum,
            scope_uncertain=scope_uncertain,
            scope_reason=scope_reason,
            reliable=not scope_uncertain,
            reason=reason,
        )
    finally:
        doc.close()


def _category_from_tables(doc: pymupdf.Document) -> ExtractedCategory | None:
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        for table in _iter_tables(page):
            for row in table.extract() or []:
                cells = [_cell_text(cell) for cell in row]
                if not cells or cells[0] != CATEGORY_LABEL:
                    continue
                raw = next((cell for cell in cells[1:] if cell), "")
                return _finish_category(
                    page,
                    page_number=page_index + 1,
                    raw=raw,
                    quote=f"{CATEGORY_LABEL} {raw}".strip(),
                )
    return None


def _category_from_text(doc: pymupdf.Document) -> ExtractedCategory:
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        lines = (page.get_text() or "").splitlines()
        for index, line in enumerate(lines):
            if not _is_category_label_line(line):
                continue
            raw = _value_after_label(line, CATEGORY_LABEL)
            if not raw:
                raw = _next_nonempty(lines, index)
            if not raw:
                continue
            if _looks_like_prose(raw):
                continue
            return _finish_category(
                page,
                page_number=page_index + 1,
                raw=raw,
                quote=f"{CATEGORY_LABEL} {raw}".strip(),
            )
    return ExtractedCategory(
        raw_value=None,
        normalized=None,
        page_number=None,
        quote=None,
        bbox=None,
        reliable=False,
        reason="未填写项目类别，不同类别上限不同，无法选择阈值",
        source="native_text",
    )


def _finish_category(
    page: pymupdf.Page,
    *,
    page_number: int,
    raw: str,
    quote: str,
) -> ExtractedCategory:
    normalized = normalize_category(raw)
    bbox = _search_bbox(page, raw) or _search_bbox(page, CATEGORY_LABEL)
    if normalized is None:
        return ExtractedCategory(
            raw_value=raw,
            normalized=None,
            page_number=page_number,
            quote=quote,
            bbox=bbox,
            reliable=False,
            reason=f"项目类别「{raw}」不是自然科学类或人文社会科学类，无法选择上限",
        )
    return ExtractedCategory(
        raw_value=raw,
        normalized=normalized,
        page_number=page_number,
        quote=quote,
        bbox=bbox,
        reliable=True,
        reason=None,
    )


def _is_category_label_line(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned:
        return False
    if cleaned in {CATEGORY_LABEL, f"{CATEGORY_LABEL}:", f"{CATEGORY_LABEL}："}:
        return True
    if cleaned.startswith(CATEGORY_LABEL) and len(cleaned) <= 24:
        return True
    return False


def _looks_like_prose(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) > 24:
        return True
    if stripped.endswith("。") or "未填写" in stripped:
        return True
    return False


def _value_after_label(line: str, label: str) -> str:
    after = line.split(label, 1)[1]
    return after.strip().lstrip(":：").strip()


def _next_nonempty(lines: list[str], index: int) -> str:
    for item in lines[index + 1 : index + 4]:
        cleaned = item.strip()
        if cleaned:
            return cleaned
    return ""


def _lines_from_tables(doc: pymupdf.Document) -> list[BudgetLine]:
    collected: list[BudgetLine] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        for table in _iter_tables(page):
            rows = table.extract() or []
            if not rows:
                continue
            headers = [_cell_text(cell) for cell in rows[0]]
            if not _is_subject_amount_table(headers):
                continue
            amount_index, default_unit = _amount_column(headers)
            if amount_index is None:
                continue
            for raw_row in rows[1:]:
                line = _line_from_row(
                    page,
                    raw_row,
                    amount_index=amount_index,
                    default_unit=default_unit,
                    page_number=page_number,
                )
                if line is not None:
                    collected.append(line)
            if collected:
                return collected
    return collected


def _lines_from_text(doc: pymupdf.Document) -> list[BudgetLine]:
    """Fallback when table detection misses a simple 科目/金额 listing."""
    collected: list[BudgetLine] = []
    in_section = False
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        lines = (page.get_text() or "").splitlines()
        pending_name: str | None = None
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if "科目预算" in stripped or (stripped == "科目"):
                in_section = True
                pending_name = None
                continue
            if in_section and re.match(r"^[二三四五六七八九十]、", stripped):
                in_section = False
                pending_name = None
                continue
            if not in_section:
                continue
            if stripped in {"科目", "金额（元）", "金额(元)", "说明"}:
                continue
            parsed_inline = _line_from_text_row(page, stripped, page_number=page_number)
            if parsed_inline is not None:
                collected.append(parsed_inline)
                pending_name = None
                continue
            if pending_name is None and not parse_amount_text(stripped, default_unit="yuan"):
                if stripped not in {"金额（元）", "说明"}:
                    pending_name = stripped
                continue
            if pending_name is not None:
                line = _line_from_name_amount(
                    page,
                    name=pending_name,
                    amount_text=stripped,
                    page_number=page_number,
                )
                collected.append(line)
                pending_name = None
    return collected


def _line_from_row(
    page: pymupdf.Page,
    raw_row: list[Any],
    *,
    amount_index: int,
    default_unit: AmountUnit | None,
    page_number: int,
) -> BudgetLine | None:
    cells = [_cell_text(cell) for cell in raw_row]
    if not any(cells):
        return None
    name = cells[0] if cells else ""
    if not name:
        return None
    amount_text = cells[amount_index] if amount_index < len(cells) else ""
    return _line_from_name_amount(
        page,
        name=name,
        amount_text=amount_text,
        page_number=page_number,
        default_unit=default_unit,
    )


def _line_from_text_row(page: pymupdf.Page, text: str, *, page_number: int) -> BudgetLine | None:
    parsed = parse_amount_text(text, default_unit="yuan")
    if parsed is None:
        return None
    name = text[: text.find(parsed.raw_number)].strip() if parsed.raw_number in text else ""
    if not name or name in {"申请总额", "申请经费", "申请金额"}:
        return None
    if any(marker in text for marker in ("其中", "未标明", "合并单元格")):
        return None
    return _line_from_name_amount(
        page,
        name=name,
        amount_text=f"{parsed.raw_number}{parsed.raw_unit or '元'}",
        page_number=page_number,
        default_unit="yuan",
    )


def _line_from_name_amount(
    page: pymupdf.Page,
    *,
    name: str,
    amount_text: str,
    page_number: int,
    default_unit: AmountUnit | None = "yuan",
) -> BudgetLine:
    is_total = _is_total_row(name)
    quote = f"{name} {amount_text}".strip()
    bbox = _search_bbox(page, amount_text) or _search_bbox(page, name)
    if any(token in name for token in MATCHING_SCOPE_NAMES):
        return BudgetLine(
            name=name,
            raw_value=amount_text or None,
            amount_yuan=None,
            is_total=is_total,
            page_number=page_number,
            quote=quote,
            bbox=bbox,
            reliable=False,
            reason=f"科目「{name}」是否计入申请总额无法确定，需人工确认",
        )
    parsed = parse_amount_text(amount_text, default_unit=default_unit) if amount_text else None
    if parsed is None and amount_text:
        parsed = parse_amount_text(amount_text.replace(",", ""), default_unit=default_unit)
    if parsed is None:
        return BudgetLine(
            name=name,
            raw_value=amount_text or None,
            amount_yuan=None,
            is_total=is_total,
            page_number=page_number,
            quote=quote,
            bbox=bbox,
            reliable=False,
            reason=f"科目「{name}」金额单位不明或无法解析，不能当作 0",
        )
    return BudgetLine(
        name=name,
        raw_value=amount_text,
        amount_yuan=parsed.amount_yuan,
        is_total=is_total,
        page_number=page_number,
        quote=quote,
        bbox=bbox,
        reliable=True,
        reason=None,
    )


def _is_subject_amount_table(headers: list[str]) -> bool:
    if len(headers) < 2:
        return False
    joined = "".join(headers)
    has_subject = any(cell == "科目" or cell.startswith("科目") for cell in headers)
    has_amount = any("金额" in cell for cell in headers)
    if not has_subject or not has_amount:
        return False
    if any(token in joined for token in _HEADER_SKIP_TOKENS):
        return False
    if any("年" in cell and "金额" not in cell for cell in headers[1:]):
        return False
    return True


def _amount_column(headers: list[str]) -> tuple[int | None, AmountUnit | None]:
    for index, cell in enumerate(headers):
        if "金额" not in cell:
            continue
        unit: AmountUnit = "yuan"
        if "万元" in cell:
            unit = "wan_yuan"
        elif "千元" in cell:
            unit = "thousand_yuan"
        return index, unit
    return None, None


def _is_total_row(name: str) -> bool:
    cleaned = name.strip()
    if cleaned in TOTAL_ROW_NAMES:
        return True
    return cleaned.endswith("合计")


def _detect_scope_uncertainty(text: str) -> tuple[bool, str | None]:
    compact = text.replace(" ", "").replace("\u3000", "")
    for marker in SCOPE_UNCERTAIN_MARKERS:
        if marker.replace(" ", "") in compact:
            return True, (
                "科目计算口径不清：存在「其中：配套经费」或合并单元格，"
                "无法判断是否计入申请总额"
            )
    for name in MATCHING_SCOPE_NAMES:
        if name in text:
            return True, f"科目计算口径不清：出现「{name}」，无法判断是否计入申请总额"
    return False, None


def _iter_tables(page: pymupdf.Page):
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 — table detector is best-effort
        return []
    tables = getattr(found, "tables", found)
    return tables or []


def _cell_text(cell: Any) -> str:
    if cell is None:
        return ""
    return str(cell).replace("\n", "").strip()


def _search_bbox(page: pymupdf.Page, needle: str) -> BBox | None:
    if not needle:
        return None
    for candidate in (needle, needle.replace(",", ""), needle.replace(" ", "")):
        if not candidate:
            continue
        hits = page.search_for(candidate)
        if hits:
            rect = hits[0]
            return BBox(
                x0=float(rect.x0),
                y0=float(rect.y0),
                x1=float(rect.x1),
                y1=float(rect.y1),
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            )
    return None
