"""Extract material-manifest, large-equipment, and ethics signals from PDFs.

RULE-001 uses the uploaded file list and processing state.
RULE-008 reads budget equipment lines (unit price / quantity) and attachment clues.
RULE-009 reads ethics/data-scope statements. Unknown values stay unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Material, MaterialCategory, MaterialStatus
from app.services import pdf as pdf_service
from app.services.funding_extract import BBox
from app.services.money import parse_amount_text
from app.services.storage import material_file_path

REQUIRED_CATEGORIES: tuple[MaterialCategory, ...] = (
    MaterialCategory.APPLICATION,
    MaterialCategory.BUDGET,
    MaterialCategory.COMMITMENT,
)

CATEGORY_LABELS = {
    MaterialCategory.APPLICATION.value: "申报书",
    MaterialCategory.BUDGET.value: "经费预算表",
    MaterialCategory.COMMITMENT.value: "科研诚信与合规承诺书",
    MaterialCategory.OTHER.value: "其他材料",
}

EQUIPMENT_THRESHOLD_YUAN = 50_000
EQUIPMENT_FEE_NAMES = frozenset({"设备费"})
ATTACHMENT_NAME_MARKERS = ("设备必要性说明", "必要性说明")
ATTACHMENT_ABSENCE_MARKERS = (
    "未提供设备必要性说明",
    "当前包内未提供设备必要性说明",
    "未提供“单台/套设备金额达到5万元时的设备必要性说明”",
    "未提供“单台/套设备金额达到5万元时的设备必要性说明",
    "当前包内未提供",
)
NO_LARGE_DEVICE_MARKERS = (
    "不存在单台/套达到5万元设备",
    "不存在单台/套达到 5 万元设备",
    "无单台/套≥5万元",
    "无单台/套≥50000",
)

EthicsKind = Literal[
    "not_involved",
    "involved",
    "unclear",
    "approval",
    "missing_proof",
    "submission_required",
]

_QTY_SET_RE = re.compile(r"(?P<qty>\d+)\s*套")
_UNIT_PRICE_RE = re.compile(r"单价\s*[:：]?\s*(?P<amount>\d{1,3}(?:,\d{3})+|\d+)\s*元")
_SHARED_TOTAL_RE = re.compile(r"共\s*(?P<amount>\d{1,3}(?:,\d{3})+|\d+)\s*元")
_MAX_UNIT_RE = re.compile(r"最高单价\s*(?P<amount>\d{1,3}(?:,\d{3})+|\d+)\s*元")
_NO_UNIT_MARKERS = ("无单价", "无单台/套单价", "无型号拆分", "无法判断是否存在单台")
_TOTAL_ROW_NAMES = frozenset({"合计", "科目合计", "总计", "小计", "总和"})

_NOT_INVOLVED_RES = (
    re.compile(r"不涉及人体受试者.{0,12}个人敏感信息.{0,12}生物样本.{0,8}受限制数据"),
    re.compile(r"不涉及人体研究"),
    re.compile(r"仅处理公开基准数据及自建非个人设备性能数据"),
)
_INVOLVED_RES = (
    re.compile(r"明确招募\d+名受试者"),
    re.compile(r"招募受试者"),
    re.compile(r"人体受试者"),
    re.compile(r"采集操作负荷"),
    re.compile(r"生物样本"),
    re.compile(r"受限制数据"),
    re.compile(r"去标识化历史脑电"),
    re.compile(r"合作单位提供的去标识化"),
)
_UNCLEAR_RES = (
    re.compile(r"是否构成个人敏感信息"),
    re.compile(r"是否需要伦理审批"),
    re.compile(r"拟在实施前.{0,24}确认"),
    re.compile(r"未附.{0,20}数据授权"),
    re.compile(r"未给出.{0,12}伦理审批编号"),
    re.compile(r"正式实验前将补齐"),
)
_APPROVAL_RES = (
    re.compile(r"伦理审批编号[:：]?\s*[A-Za-z0-9\-]+"),
    re.compile(r"伦理审查进行中"),
    re.compile(r"审批进行中证明"),
)
_APPROVAL_NEG_MARKERS = (
    "未给出",
    "既无审批编号",
    "既无审批",
    "无进行中证明",
    "提交时提供伦理审批编号",
    "应提供伦理",
)
_HYPOTHETICAL_MARKERS = ("如后续", "确需引入", "审批完成前", "不启动超出")
_MISSING_PROOF_RES = (
    re.compile(r"既无审批编号也无进行中证明"),
    re.compile(r"无伦理编号"),
    re.compile(r"未给出学校伦理审批编号"),
)
_SUBMISSION_REQUIRED_RES = (
    re.compile(r"政策要求提交时提供"),
    re.compile(r"提交时提供伦理审批"),
    re.compile(r"提交时提供.{0,12}伦理"),
)


@dataclass(frozen=True, slots=True)
class MaterialManifest:
    """Uploaded files for one project, including processing/failed rows."""

    materials: tuple[Material, ...]

    @property
    def processing(self) -> tuple[Material, ...]:
        return tuple(item for item in self.materials if item.status == MaterialStatus.PROCESSING.value)

    @property
    def failed(self) -> tuple[Material, ...]:
        return tuple(item for item in self.materials if item.status == MaterialStatus.FAILED.value)

    @property
    def ready(self) -> tuple[Material, ...]:
        return tuple(item for item in self.materials if item.status == MaterialStatus.READY.value)

    @property
    def frozen(self) -> bool:
        """True only when at least one file exists and none is still uploading."""
        return bool(self.materials) and not self.processing

    def ready_in(self, category: MaterialCategory) -> tuple[Material, ...]:
        return tuple(item for item in self.ready if item.category == category.value)

    def all_in(self, category: MaterialCategory) -> tuple[Material, ...]:
        return tuple(item for item in self.materials if item.category == category.value)


@dataclass(frozen=True, slots=True)
class EquipmentItem:
    """One device / 设备费 clue used by RULE-008."""

    name: str
    quantity: int | None
    unit_price_yuan: int | None
    total_yuan: int | None
    unit_price_known: bool
    page_number: int | None
    quote: str | None
    bbox: BBox | None
    reason: str | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "unit_price_yuan": self.unit_price_yuan,
            "total_yuan": self.total_yuan,
            "unit_price_known": self.unit_price_known,
            "page_number": self.page_number,
            "quote": self.quote,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ExtractedEquipment:
    """Budget-side equipment picture for the ≥5-万元 attachment rule."""

    items: tuple[EquipmentItem, ...]
    equipment_fee_yuan: int | None
    equipment_fee_quote: str | None
    max_unit_price_yuan: int | None
    no_large_device_stated: bool
    unit_price_uncertain: bool
    uncertain_reason: str | None
    absence_stated: bool
    absence_quote: str | None
    page_number: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "equipment_fee_yuan": self.equipment_fee_yuan,
            "equipment_fee_quote": self.equipment_fee_quote,
            "max_unit_price_yuan": self.max_unit_price_yuan,
            "no_large_device_stated": self.no_large_device_stated,
            "unit_price_uncertain": self.unit_price_uncertain,
            "uncertain_reason": self.uncertain_reason,
            "absence_stated": self.absence_stated,
            "absence_quote": self.absence_quote,
            "page_number": self.page_number,
        }


@dataclass(frozen=True, slots=True)
class AttachmentHit:
    material_id: str
    category: str
    original_filename: str
    page_number: int | None
    quote: str | None
    bbox: BBox | None
    present: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class EthicsSignal:
    kind: EthicsKind
    quote: str
    page_number: int | None
    bbox: BBox | None
    material_id: str
    category: str
    original_filename: str
    reliable: bool = True
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "quote": self.quote,
            "page_number": self.page_number,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "material_id": self.material_id,
            "category": self.category,
            "original_filename": self.original_filename,
            "reliable": self.reliable,
            "reason": self.reason,
        }


def load_manifest(db: Session, project_id: str) -> MaterialManifest:
    statement = (
        select(Material)
        .where(Material.project_id == project_id)
        .order_by(Material.created_at.asc())
    )
    return MaterialManifest(materials=tuple(db.scalars(statement).all()))


def extract_equipment(path: Path) -> ExtractedEquipment:
    """Read 设备费 / 设备明细. Does not infer unit price from a shared total."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return ExtractedEquipment(
            items=(),
            equipment_fee_yuan=None,
            equipment_fee_quote=None,
            max_unit_price_yuan=None,
            no_large_device_stated=False,
            unit_price_uncertain=True,
            uncertain_reason=f"无法读取预算 PDF：{exc.message}",
            absence_stated=False,
            absence_quote=None,
            page_number=None,
        )
    try:
        items: list[EquipmentItem] = []
        fee_yuan: int | None = None
        fee_quote: str | None = None
        fee_page: int | None = None
        no_large = False
        absence = False
        absence_quote: str | None = None
        max_unit: int | None = None
        uncertain = False
        uncertain_reason: str | None = None

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_number = page_index + 1
            page_text = page.get_text() or ""
            compact = _compact(page_text)
            if any(_compact(marker) in compact for marker in NO_LARGE_DEVICE_MARKERS):
                no_large = True
            if any(_compact(marker) in compact for marker in ATTACHMENT_ABSENCE_MARKERS):
                absence = True
                absence_quote = _quote_around(page_text, "设备必要性说明") or _quote_around(
                    page_text, "当前包内未提供"
                )
            max_hit = _MAX_UNIT_RE.search(page_text.replace(" ", ""))
            if max_hit:
                parsed_max = _parse_yuan(max_hit.group("amount"))
                if parsed_max is not None:
                    max_unit = parsed_max if max_unit is None else max(max_unit, parsed_max)

            for table in _iter_tables(page):
                rows = table.extract() or []
                if not rows:
                    continue
                headers = [_cell_text(cell) for cell in rows[0]]
                if _is_equipment_detail_table(headers):
                    items.extend(
                        _items_from_detail_table(page, rows, headers, page_number=page_number)
                    )
                    continue
                if _is_subject_table(headers):
                    amount_index, note_index = _subject_columns(headers)
                    for raw_row in rows[1:]:
                        cells = [_cell_text(cell) for cell in raw_row]
                        if not cells:
                            continue
                        name = cells[0]
                        if name not in EQUIPMENT_FEE_NAMES:
                            continue
                        amount_text = cells[amount_index] if amount_index < len(cells) else ""
                        note = cells[note_index] if 0 <= note_index < len(cells) else ""
                        parsed = parse_amount_text(amount_text, default_unit="yuan")
                        if parsed is not None:
                            fee_yuan = parsed.amount_yuan
                            fee_page = page_number
                            fee_quote = " ".join(part for part in (name, amount_text, note) if part)
                        item = _item_from_note(
                            page,
                            name=name,
                            note=note or amount_text,
                            fee_yuan=parsed.amount_yuan if parsed else None,
                            page_number=page_number,
                            source="subject_note",
                        )
                        if item is not None:
                            items.append(item)

            for line in page_text.splitlines():
                stripped = line.strip()
                if "设备明细" in stripped or ("单价" in stripped and "套" in stripped) or (
                    "套" in stripped and "共" in stripped
                ):
                    item = _item_from_note(
                        page,
                        name="设备明细",
                        note=stripped,
                        fee_yuan=None,
                        page_number=page_number,
                        source="prose",
                    )
                    if item is not None:
                        items.append(item)
                if any(marker in stripped for marker in _NO_UNIT_MARKERS):
                    uncertain = True
                    uncertain_reason = stripped

        known_units = [item.unit_price_yuan for item in items if item.unit_price_known and item.unit_price_yuan is not None]
        if known_units:
            computed_max = max(known_units)
            max_unit = computed_max if max_unit is None else max(max_unit, computed_max)
        unknown_units = [item for item in items if not item.unit_price_known]
        if unknown_units:
            uncertain = True
            if uncertain_reason is None:
                sample = unknown_units[0]
                uncertain_reason = sample.reason or "设备单价无法确定"

        return ExtractedEquipment(
            items=tuple(items),
            equipment_fee_yuan=fee_yuan,
            equipment_fee_quote=fee_quote,
            max_unit_price_yuan=max_unit,
            no_large_device_stated=no_large,
            unit_price_uncertain=uncertain,
            uncertain_reason=uncertain_reason,
            absence_stated=absence,
            absence_quote=absence_quote,
            page_number=fee_page,
        )
    finally:
        doc.close()


def find_equipment_attachment(
    db: Session,
    manifest: MaterialManifest,
    settings: Settings | None = None,
) -> AttachmentHit | None:
    """A dedicated 设备必要性说明 file. Mentions of「未提供」do not count as present."""
    cfg = settings or get_settings()
    for material in manifest.ready:
        filename = material.original_filename or ""
        if any(marker in filename for marker in ATTACHMENT_NAME_MARKERS) and "未提供" not in filename:
            return AttachmentHit(
                material_id=material.id,
                category=material.category,
                original_filename=filename,
                page_number=1,
                quote=filename,
                bbox=None,
                present=True,
                reason=None,
            )
        if material.category != MaterialCategory.OTHER.value:
            continue
        path = material_file_path(material.id, cfg)
        try:
            doc = pdf_service.open_document(path)
        except pdf_service.PdfError:
            continue
        try:
            first = doc.load_page(0)
            text = first.get_text() or ""
            if "设备必要性说明" in text and not _mentions_absence(text):
                return AttachmentHit(
                    material_id=material.id,
                    category=material.category,
                    original_filename=filename,
                    page_number=1,
                    quote=_quote_around(text, "设备必要性说明") or "设备必要性说明",
                    bbox=_search_bbox(first, "设备必要性说明"),
                    present=True,
                    reason=None,
                )
        finally:
            doc.close()
    return None


def collect_attachment_absence(
    manifest: MaterialManifest,
    settings: Settings | None = None,
) -> tuple[AttachmentHit, ...]:
    """Quotes that say the equipment-necessity attachment is not in the pack."""
    cfg = settings or get_settings()
    hits: list[AttachmentHit] = []
    for material in manifest.ready:
        if material.category not in {
            MaterialCategory.BUDGET.value,
            MaterialCategory.COMMITMENT.value,
        }:
            continue
        path = material_file_path(material.id, cfg)
        try:
            doc = pdf_service.open_document(path)
        except pdf_service.PdfError:
            continue
        try:
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                text = page.get_text() or ""
                if not _mentions_absence(text):
                    continue
                quote = (
                    _quote_around(text, "设备必要性说明")
                    or _quote_around(text, "当前包内未提供")
                    or "当前包内未提供设备必要性说明"
                )
                hits.append(
                    AttachmentHit(
                        material_id=material.id,
                        category=material.category,
                        original_filename=material.original_filename,
                        page_number=page_index + 1,
                        quote=quote,
                        bbox=_search_bbox(page, "设备必要性说明") or _search_bbox(page, "当前包内未提供"),
                        present=False,
                        reason="完整范围内确认未提供该附件",
                    )
                )
                break
        finally:
            doc.close()
    return tuple(hits)


def extract_ethics_signals(
    material: Material,
    path: Path,
) -> tuple[EthicsSignal, ...]:
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return (
            EthicsSignal(
                kind="unclear",
                quote="",
                page_number=None,
                bbox=None,
                material_id=material.id,
                category=material.category,
                original_filename=material.original_filename,
                reliable=False,
                reason=f"无法读取材料：{exc.message}",
            ),
        )
    try:
        collected: list[EthicsSignal] = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            text = page.get_text() or ""
            if not text.strip():
                continue
            collected.extend(
                _signals_from_page(
                    material,
                    page,
                    text=text,
                    page_number=page_index + 1,
                )
            )
        return tuple(collected)
    finally:
        doc.close()


def _signals_from_page(
    material: Material,
    page: pymupdf.Page,
    *,
    text: str,
    page_number: int,
) -> list[EthicsSignal]:
    compact = _compact(text)
    found: list[EthicsSignal] = []
    specs: tuple[tuple[EthicsKind, tuple[re.Pattern[str], ...]], ...] = (
        ("not_involved", _NOT_INVOLVED_RES),
        ("involved", _INVOLVED_RES),
        ("unclear", _UNCLEAR_RES),
        ("approval", _APPROVAL_RES),
        ("missing_proof", _MISSING_PROOF_RES),
        ("submission_required", _SUBMISSION_REQUIRED_RES),
    )
    for kind, patterns in specs:
        for pattern in patterns:
            match = pattern.search(compact) or pattern.search(text)
            if match is None:
                continue
            snippet = match.group(0)
            quote = _sentence_containing(text, snippet) or snippet
            if kind == "involved" and (
                _negated_trigger(compact, snippet) or _hypothetical(compact, snippet)
            ):
                continue
            if kind == "approval" and _approval_is_non_hit(compact, snippet, text):
                continue
            found.append(
                EthicsSignal(
                    kind=kind,
                    quote=quote.strip(),
                    page_number=page_number,
                    bbox=_search_bbox(page, snippet[:20]) or _search_bbox(page, quote[:20]),
                    material_id=material.id,
                    category=material.category,
                    original_filename=material.original_filename,
                )
            )
    return found


def _negated_trigger(compact: str, snippet: str) -> bool:
    """「不涉及人体受试者、…、受限制数据」covers the whole list, not only the first term."""
    needle = _compact(snippet)
    index = compact.find(needle)
    if index < 0:
        return False
    window = compact[max(0, index - 40) : index]
    if "不涉及" not in window:
        return False
    after_negation = window.split("不涉及")[-1]
    return "。" not in after_negation


def _hypothetical(compact: str, snippet: str) -> bool:
    needle = _compact(snippet)
    index = compact.find(needle)
    if index < 0:
        return False
    window = compact[max(0, index - 30) : index + len(needle)]
    return any(marker in window for marker in _HYPOTHETICAL_MARKERS)


def _approval_is_non_hit(compact: str, snippet: str, original: str) -> bool:
    """Requirement or absence of an approval number is not proof that one exists."""
    haystack = compact + _compact(original)
    if any(_compact(marker) in haystack for marker in _APPROVAL_NEG_MARKERS):
        # Only reject when the match itself sits in an absence/requirement clause.
        needle = _compact(snippet)
        index = compact.find(needle)
        if index < 0:
            return True
        window = compact[max(0, index - 24) : index + len(needle) + 12]
        return any(_compact(marker) in window for marker in _APPROVAL_NEG_MARKERS)
    return False


def _item_from_note(
    page: pymupdf.Page,
    *,
    name: str,
    note: str,
    fee_yuan: int | None,
    page_number: int,
    source: str,
) -> EquipmentItem | None:
    compact = _compact(note)
    if not _looks_like_device_line(note, compact):
        return None
    quantity = _first_int(_QTY_SET_RE.search(note) or _QTY_SET_RE.search(compact))
    unit_price = None
    total = fee_yuan
    unit_known = False
    reason = None

    unit_match = _UNIT_PRICE_RE.search(note) or _UNIT_PRICE_RE.search(compact)
    if unit_match:
        unit_price = _parse_yuan(unit_match.group("amount"))
        unit_known = unit_price is not None
    shared = _SHARED_TOTAL_RE.search(note) or _SHARED_TOTAL_RE.search(compact)
    if shared:
        total = _parse_yuan(shared.group("amount"))
        if quantity is not None and quantity != 1:
            unit_known = False
            unit_price = None
            reason = "设备只有总价，数量/单价不可确定"
    if any(marker in note or _compact(marker) in compact for marker in _NO_UNIT_MARKERS):
        unit_known = False
        unit_price = None
        reason = reason or "设备单价或型号拆分缺失，无法判断是否达到5万元"

    if not unit_known and quantity == 1 and fee_yuan is not None and shared is None:
        unit_price = fee_yuan
        total = fee_yuan
        unit_known = True
    if not unit_known and quantity == 1 and unit_match is None:
        amounts = list(_AMOUNT_RE.finditer(note))
        if len(amounts) == 1 and "共" not in note:
            parsed = _parse_yuan(amounts[0].group("amount"))
            if parsed is not None:
                unit_price = parsed
                total = parsed
                unit_known = True

    quote = f"{name} {note}".strip()
    return EquipmentItem(
        name=name,
        quantity=quantity,
        unit_price_yuan=unit_price,
        total_yuan=total,
        unit_price_known=unit_known,
        page_number=page_number,
        quote=quote,
        bbox=_search_bbox(page, note[:24]) or _search_bbox(page, name),
        reason=reason,
        source=source,
    )


_AMOUNT_RE = re.compile(r"(?P<amount>\d{1,3}(?:,\d{3})+|\d+)\s*元")


def _looks_like_device_line(note: str, compact: str) -> bool:
    if any(marker in note or _compact(marker) in compact for marker in _NO_UNIT_MARKERS):
        return True
    if _QTY_SET_RE.search(note) or _QTY_SET_RE.search(compact):
        return True
    if "单价" in note or "单价" in compact:
        return True
    if "共" in note and "元" in note:
        return True
    return False


def _items_from_detail_table(
    page: pymupdf.Page,
    rows: list[Any],
    headers: list[str],
    *,
    page_number: int,
) -> list[EquipmentItem]:
    name_i = 0
    qty_i = _header_index(headers, "数量")
    price_i = _header_index(headers, "单价")
    total_i = _header_index(headers, "小计")
    collected: list[EquipmentItem] = []
    for raw_row in rows[1:]:
        cells = [_cell_text(cell) for cell in raw_row]
        if not cells or not cells[0] or cells[0] in _TOTAL_ROW_NAMES:
            continue
        name = cells[0]
        qty = _parse_int(cells[qty_i]) if qty_i >= 0 and qty_i < len(cells) else None
        unit = _parse_yuan(cells[price_i]) if price_i >= 0 and price_i < len(cells) else None
        total = _parse_yuan(cells[total_i]) if total_i >= 0 and total_i < len(cells) else None
        quote = " ".join(cell for cell in cells if cell)
        collected.append(
            EquipmentItem(
                name=name,
                quantity=qty,
                unit_price_yuan=unit,
                total_yuan=total,
                unit_price_known=unit is not None,
                page_number=page_number,
                quote=quote,
                bbox=_search_bbox(page, name) or _search_bbox(page, cells[price_i] if price_i >= 0 else ""),
                reason=None if unit is not None else "测算单价无法解析",
                source="detail_table",
            )
        )
    return collected


def _is_equipment_detail_table(headers: list[str]) -> bool:
    joined = "".join(headers)
    return "数量" in joined and "单价" in joined


def _is_subject_table(headers: list[str]) -> bool:
    has_subject = any(cell == "科目" or cell.startswith("科目") for cell in headers)
    has_amount = any("金额" in cell for cell in headers)
    return has_subject and has_amount


def _subject_columns(headers: list[str]) -> tuple[int, int]:
    amount_index = next((i for i, cell in enumerate(headers) if "金额" in cell), 1)
    note_index = next((i for i, cell in enumerate(headers) if "说明" in cell), -1)
    return amount_index, note_index


def _header_index(headers: list[str], token: str) -> int:
    for index, cell in enumerate(headers):
        if token in cell:
            return index
    return -1


def _mentions_absence(text: str) -> bool:
    compact = _compact(text)
    return any(_compact(marker) in compact for marker in ATTACHMENT_ABSENCE_MARKERS)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("\u3000", "")


def _sentence_containing(text: str, needle: str) -> str | None:
    compact_needle = _compact(needle)[:16]
    for raw in re.split(r"(?<=[。；;])", text):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if needle[:8] in line or compact_needle in _compact(line):
            return line[:180]
    return _quote_around(text, needle[:12])


def _quote_around(text: str, needle: str, radius: int = 40) -> str | None:
    if not needle:
        return None
    index = text.find(needle)
    if index < 0:
        compact_source = text
        compact_needle = needle.replace(" ", "")
        index = compact_source.replace(" ", "").find(compact_needle)
        if index < 0:
            return None
        # Fall back to a nearby original line.
        for line in text.splitlines():
            if needle[:6] in line or _compact(needle[:8]) in _compact(line):
                return line.strip()
        return needle
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _parse_yuan(raw: str | None) -> int | None:
    if not raw:
        return None
    parsed = parse_amount_text(str(raw), default_unit="yuan")
    return parsed.amount_yuan if parsed else None


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    if match is None:
        return None
    return int(match.group(0))


def _first_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    try:
        return int(match.group("qty"))
    except (IndexError, ValueError, TypeError):
        return None


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
    for candidate in (needle, needle.replace(",", ""), needle.replace(" ", ""), needle[:12]):
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
