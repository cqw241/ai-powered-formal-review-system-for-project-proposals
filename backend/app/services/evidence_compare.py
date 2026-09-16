"""B11 presentation: issue detail, dual-document compare, scan-region highlight.

Reuses stored ReviewEvidence. Does not change B07/B08/B09 (or RULE-007) judgments.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.review_contract import ReviewEvidence, ReviewEvidenceBBox, RuleExecutionResult
from app.schemas import CompareSide, EvidenceBBox, EvidenceCompareView, LabeledFundingField
from app.services import pdf as pdf_service
from app.services.funding_extract import classify_label
from app.services.money import UNIT_LABELS, parse_amount_text

CHECK_FIELD_BY_RULE = {
    "RULE-002": "项目名称",
    "RULE-003": "项目负责人",
    "RULE-004": "执行期",
    "RULE-005": "申请经费",
    "RULE-006": "预算科目合计",
    "RULE-007": "申请经费",
    "RULE-010": "签署日期",
}

AMOUNT_FIELD_NAMES = {
    "申请经费",
    "申请总额",
    "申请金额",
    "申请资助经费",
    "申报书申请经费",
    "预算申请总额",
    "科目合计",
    "经费上限",
    "项目总经费",
    "总经费",
    "项目经费总额",
    "经费总额",
    "配套经费",
    "自筹经费",
}

APPLICATION_FIELD_NAMES = {
    "申请经费",
    "申请总额",
    "申请金额",
    "申请资助经费",
    "申报书申请经费",
    "预算申请总额",
}

TOTAL_FIELD_NAMES = {
    "项目总经费",
    "总经费",
    "项目经费总额",
    "经费总额",
}

COMPARE_FIELD_PRIORITY = {
    "RULE-005": ("申请经费", "申请总额", "经费上限"),
    "RULE-006": ("申请总额", "科目合计"),
    "RULE-007": ("申请经费", "申请总额", "项目总经费", "总经费"),
}

NEAR_PT = 72.0


def bbox_to_percent(bbox: ReviewEvidenceBBox | EvidenceBBox) -> dict[str, float]:
    """Map page-space bbox to percentages of the displayed page.

    Percentages are independent of CSS/display zoom; a scaled frame that
    contains both the page image and the overlay stays aligned.
    """
    width = float(bbox.page_width)
    height = float(bbox.page_height)
    if width <= 0 or height <= 0:
        return {"left": 0.0, "top": 0.0, "width": 0.0, "height": 0.0}
    return {
        "left": (float(bbox.x0) / width) * 100.0,
        "top": (float(bbox.y0) / height) * 100.0,
        "width": ((float(bbox.x1) - float(bbox.x0)) / width) * 100.0,
        "height": ((float(bbox.y1) - float(bbox.y0)) / height) * 100.0,
    }


def highlight_display_rect(
    bbox: ReviewEvidenceBBox | EvidenceBBox,
    *,
    displayed_width: float,
    displayed_height: float,
) -> dict[str, float]:
    """Pixel rect of a highlight on a displayed page of the given size."""
    percent = bbox_to_percent(bbox)
    return {
        "left": percent["left"] / 100.0 * displayed_width,
        "top": percent["top"] / 100.0 * displayed_height,
        "width": percent["width"] / 100.0 * displayed_width,
        "height": percent["height"] / 100.0 * displayed_height,
    }


def classify_compare_field(field_name: str | None, field_kind: str | None = None) -> str | None:
    """Distinguish 申请经费 vs 总经费 for display. Not a rule judgment."""
    if field_kind in {"application_funding", "total_funding", "matching_funding"}:
        return field_kind
    name = (field_name or "").strip()
    if not name:
        return None
    if name in TOTAL_FIELD_NAMES or ("总经费" in name and "申请" not in name):
        return "total_funding"
    if name in APPLICATION_FIELD_NAMES:
        return "application_funding"
    if name in {"配套经费", "自筹经费"}:
        return "matching_funding"
    classified = classify_label(name)
    return None if classified == "unknown" else classified


def infer_unit(evidence: ReviewEvidence) -> str | None:
    raw = evidence.raw_value or evidence.quote or ""
    parsed = parse_amount_text(raw) if raw else None
    if parsed is not None:
        return parsed.raw_unit or UNIT_LABELS[parsed.unit]
    field_kind = classify_compare_field(evidence.field_name)
    if field_kind in {"application_funding", "total_funding", "matching_funding"}:
        if evidence.normalized_value is not None:
            return "元"
        return None
    if evidence.field_name in AMOUNT_FIELD_NAMES and evidence.normalized_value is not None:
        return "元"
    return None


def expand_recognition_bbox(
    path: Path,
    page_number: int,
    bbox: ReviewEvidenceBBox | EvidenceBBox | None,
) -> ReviewEvidenceBBox | None:
    """Union the text bbox with nearby scan images / stamp drawings on the same page."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError:
        return _copy_bbox(bbox)
    try:
        if page_number < 1 or page_number > doc.page_count:
            return _copy_bbox(bbox)
        page = doc.load_page(page_number - 1)
        page_w = float(page.rect.width)
        page_h = float(page.rect.height)
        seed = None if bbox is None else (float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1))
        regions: list[tuple[float, float, float, float]] = []
        if seed is not None:
            regions.append(seed)

        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 1:
                continue
            rect = _block_rect(block.get("bbox"))
            if rect is None:
                continue
            if seed is not None and _near(seed, rect, NEAR_PT):
                regions.append(rect)

        for drawing in page.get_drawings():
            raw_rect = drawing.get("rect")
            if raw_rect is None:
                continue
            rect = (float(raw_rect.x0), float(raw_rect.y0), float(raw_rect.x1), float(raw_rect.y1))
            if (rect[2] - rect[0]) > page_w * 0.8 and (rect[3] - rect[1]) > page_h * 0.8:
                continue
            if seed is not None and _near(seed, rect, NEAR_PT):
                regions.append(rect)

        if not regions:
            return _copy_bbox(bbox)
        x0 = max(0.0, min(item[0] for item in regions))
        y0 = max(0.0, min(item[1] for item in regions))
        x1 = min(page_w, max(item[2] for item in regions))
        y1 = min(page_h, max(item[3] for item in regions))
        if x1 <= x0 or y1 <= y0:
            return _copy_bbox(bbox)
        return ReviewEvidenceBBox(
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            page_width=page_w,
            page_height=page_h,
        )
    finally:
        doc.close()


def list_labeled_funding_fields(
    path: Path,
    *,
    material_id: str | None = None,
    original_filename: str | None = None,
) -> list[LabeledFundingField]:
    """List 申请经费 / 总经费 mentions from a PDF without choosing a compare target."""
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError:
        return []
    found: list[LabeledFundingField] = []
    seen: set[tuple[str, str, int]] = set()
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_number = page_index + 1
            text = page.get_text() or ""
            for match in _LABEL_AMOUNT.finditer(text):
                label = match.group("label")
                amount = match.group("amount")
                unit = match.group("unit") or ""
                kind = classify_label(label)
                if kind == "unknown":
                    continue
                raw = f"{amount} {unit}".strip()
                key = (label, raw, page_number)
                if key in seen:
                    continue
                seen.add(key)
                bbox = _search_bbox(page, (f"{amount} {unit}".strip(), amount, label))
                found.append(
                    LabeledFundingField(
                        field_name=label,
                        field_kind=kind,
                        raw_value=raw,
                        unit=unit or None,
                        page_number=page_number,
                        bbox=_to_schema_bbox(bbox),
                        material_id=material_id,
                        original_filename=original_filename,
                    )
                )
    finally:
        doc.close()
    return found


def build_compare_view(
    result: RuleExecutionResult,
    *,
    material_paths: dict[str, Path] | None = None,
) -> EvidenceCompareView:
    """Project a stored rule result into the dual-document compare payload."""
    data = result.data or {}
    rule_code = str(data.get("rule_code") or data.get("rule_id") or "")
    check_field = str(data.get("check_field") or CHECK_FIELD_BY_RULE.get(rule_code) or "核对字段")
    difference, difference_yuan = _difference(result)
    selected = _select_sides(result, rule_code)
    paths = material_paths or {}

    sides: list[CompareSide] = []
    for evidence in selected:
        display_bbox = evidence.bbox
        path = paths.get(evidence.material_id) if evidence.material_id else None
        if path is not None and evidence.page_number:
            display_bbox = expand_recognition_bbox(path, evidence.page_number, evidence.bbox) or evidence.bbox
        field_kind = classify_compare_field(evidence.field_name)
        sides.append(
            CompareSide(
                material_id=evidence.material_id,
                category=evidence.category,
                original_filename=evidence.original_filename,
                field_name=evidence.field_name or check_field,
                field_kind=field_kind,
                raw_value=evidence.raw_value,
                unit=infer_unit(evidence),
                normalized_value=evidence.normalized_value,
                page_number=evidence.page_number,
                quote=evidence.quote,
                bbox=_to_schema_bbox(display_bbox),
                reliable=evidence.reliable,
                reason=evidence.reason,
                openable=bool(evidence.material_id and evidence.page_number),
            )
        )

    funding_fields: list[LabeledFundingField] = []
    seen_materials: set[str] = set()
    for evidence in result.evidence:
        material_id = evidence.material_id
        if not material_id or material_id in seen_materials:
            continue
        path = paths.get(material_id)
        if path is None:
            continue
        seen_materials.add(material_id)
        funding_fields.extend(
            list_labeled_funding_fields(
                path,
                material_id=material_id,
                original_filename=evidence.original_filename,
            )
        )

    return EvidenceCompareView(
        check_field=check_field,
        difference=difference,
        difference_yuan=difference_yuan,
        sides=sides,
        funding_fields=funding_fields,
    )


def _select_sides(result: RuleExecutionResult, rule_code: str) -> list[ReviewEvidence]:
    evidence = list(result.evidence)
    preferred_names = COMPARE_FIELD_PRIORITY.get(rule_code)
    if preferred_names:
        preferred = [item for item in evidence if item.field_name in preferred_names]
        if len(preferred) >= 2:
            return preferred
        if preferred:
            return preferred
    return evidence


def _difference(result: RuleExecutionResult) -> tuple[str | None, int | None]:
    data = result.data or {}
    yuan = data.get("difference_yuan")
    if isinstance(yuan, int):
        return f"{yuan} 元", yuan
    if isinstance(yuan, float) and yuan.is_integer():
        return f"{int(yuan)} 元", int(yuan)

    values: list[str] = []
    seen: set[str] = set()
    for evidence in result.evidence:
        if evidence.normalized_value is None or evidence.normalized_value == "":
            continue
        text = str(evidence.normalized_value)
        if text in seen:
            continue
        seen.add(text)
        values.append(text)
    if len(values) >= 2:
        return " ≠ ".join(f"「{item}」" for item in values), None
    return None, None


def _copy_bbox(bbox: ReviewEvidenceBBox | EvidenceBBox | None) -> ReviewEvidenceBBox | None:
    if bbox is None:
        return None
    return ReviewEvidenceBBox(
        x0=float(bbox.x0),
        y0=float(bbox.y0),
        x1=float(bbox.x1),
        y1=float(bbox.y1),
        page_width=float(bbox.page_width),
        page_height=float(bbox.page_height),
    )


def _to_schema_bbox(bbox: ReviewEvidenceBBox | EvidenceBBox | None) -> EvidenceBBox | None:
    if bbox is None:
        return None
    return EvidenceBBox(
        x0=float(bbox.x0),
        y0=float(bbox.y0),
        x1=float(bbox.x1),
        y1=float(bbox.y1),
        page_width=float(bbox.page_width),
        page_height=float(bbox.page_height),
    )


def _block_rect(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        x0, y0, x1, y1 = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError, IndexError):
        return None
    return (x0, y0, x1, y1)


def _near(
    seed: tuple[float, float, float, float],
    other: tuple[float, float, float, float],
    pad: float,
) -> bool:
    ax0, ay0, ax1, ay1 = seed
    bx0, by0, bx1, by1 = other
    ax0 -= pad
    ay0 -= pad
    ax1 += pad
    ay1 += pad
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def _search_bbox(page: Any, needles: tuple[str, ...]) -> ReviewEvidenceBBox | None:
    for needle in needles:
        cleaned = needle.strip()
        if not cleaned:
            continue
        hits = page.search_for(cleaned)
        if hits:
            rect = hits[0]
            return ReviewEvidenceBBox(
                x0=float(rect.x0),
                y0=float(rect.y0),
                x1=float(rect.x1),
                y1=float(rect.y1),
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            )
    return None


_LABEL_AMOUNT = re.compile(
    r"(?P<label>申请经费|申请总额|申请金额|申请资助经费|项目总经费|总经费|项目经费总额|经费总额|配套经费|自筹经费)"
    r"\s*[:：]?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>人民币元|万元|千元|万|元)",
)
