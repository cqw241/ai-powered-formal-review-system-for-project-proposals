"""Extract project names and principal names from PDF materials.

Primary path: native PDF text + geometry search.
Unreadable, handwritten, or scan-only fields stay unreliable — callers must
not treat a missing value as a confirmed conflict.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf

from app.services import pdf as pdf_service

FieldKind = Literal["project_name", "principal"]

_FIELD_LABELS = (
    "项目名称",
    "项目类别",
    "项目负责人",
    "所在单位",
    "研究方向",
    "执行期",
    "申请经费",
    "申请总额",
    "签署日期",
    "预算原则",
)

_NOT_PROJECT_NAMES = {
    "科研诚信与合规承诺书",
    "项目申报书",
    "经费预算表",
}

_FILE_HEADING = re.compile(r"^文件\s+\S+\s+(项目申报书|经费预算表)")
_PERSON_NAME = re.compile(r"^[\u4e00-\u9fff·]{2,4}$")
_PERSON_NAME_PREFIX = re.compile(r"^([\u4e00-\u9fff·]{2,4})")
_PI_INLINE = re.compile(r"^项目负责人\s*[:：]\s*(.+)$")
_TITLE_INLINE = re.compile(r"^项目名称\s*[:：]\s*(.+)$")

_TITLE_UNREADABLE_MARKERS = (
    "原生文本层无完整标题",
    "原生文本无完整标题",
    "标题扫描件",
)
_PRINCIPAL_UNREADABLE_MARKERS = (
    "原生文本不含可区分姓名",
    "手写，原生文本",
    "手写负责人",
)


@dataclass(frozen=True, slots=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractedIdentity:
    """One material's extracted identity field, or an unresolved attempt."""

    field_kind: FieldKind
    field_label: str
    raw_value: str | None
    normalized_value: str | None
    page_number: int | None
    quote: str | None
    bbox: BBox | None
    reliable: bool
    reason: str | None
    source: Literal["native_text", "none"] = "native_text"

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_kind": self.field_kind,
            "field_label": self.field_label,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "page_number": self.page_number,
            "quote": self.quote,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "reliable": self.reliable,
            "reason": self.reason,
            "source": self.source,
        }


def normalize_project_name(value: str) -> str:
    """Unicode whitespace normalize, strip, collapse, drop CJK wrap spaces.

    Does not rewrite synonyms. Names that differ only by whitespace compare equal.
    """
    return _normalize_whitespace(value, join_cjk=True)


def normalize_person_name(value: str) -> str:
    """Whitespace-only normalize for a person name. No synonym rewriting."""
    return _normalize_whitespace(value, join_cjk=True)


def extract_project_name_from_pdf(path: Path) -> ExtractedIdentity:
    """Read the project title from one PDF. Unreadable titles stay unreliable."""
    return _extract_field(
        path,
        field_kind="project_name",
        field_label="项目名称",
        missing_reason="未能在材料中可靠读取「项目名称」",
        unreadable_reason="项目名称无法从原生文本稳定识别，需人工确认",
        finder=_find_project_name,
        unreadable_markers=_TITLE_UNREADABLE_MARKERS,
    )


def extract_principal_from_pdf(path: Path) -> ExtractedIdentity:
    """Read the principal investigator name. Handwritten/scan names stay unreliable."""
    return _extract_field(
        path,
        field_kind="principal",
        field_label="项目负责人",
        missing_reason="未能在材料中可靠读取「项目负责人」",
        unreadable_reason="项目负责人手写或扫描无法稳定识别，需人工确认",
        finder=_find_principal,
        unreadable_markers=_PRINCIPAL_UNREADABLE_MARKERS,
    )


def _normalize_whitespace(value: str, *, join_cjk: bool) -> str:
    if not value:
        return ""
    mapped: list[str] = []
    for char in value:
        if char.isspace() or unicodedata.category(char) == "Zs":
            mapped.append(" ")
        else:
            mapped.append(char)
    collapsed = re.sub(r" +", " ", "".join(mapped)).strip()
    if join_cjk:
        collapsed = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "", collapsed)
    return collapsed


def _extract_field(
    path: Path,
    *,
    field_kind: FieldKind,
    field_label: str,
    missing_reason: str,
    unreadable_reason: str,
    finder: Callable[
        [pymupdf.Document],
        list[tuple[int, str, str, str, BBox | None]],
    ],
    unreadable_markers: tuple[str, ...],
) -> ExtractedIdentity:
    try:
        doc = pdf_service.open_document(path)
    except pdf_service.PdfError as exc:
        return ExtractedIdentity(
            field_kind=field_kind,
            field_label=field_label,
            raw_value=None,
            normalized_value=None,
            page_number=None,
            quote=None,
            bbox=None,
            reliable=False,
            reason=f"无法读取 PDF：{exc.message}",
            source="none",
        )

    try:
        marker_hit = _first_marker_hit(doc, unreadable_markers)
        if marker_hit is not None:
            page_number, quote, bbox = marker_hit
            return ExtractedIdentity(
                field_kind=field_kind,
                field_label=field_label,
                raw_value=None,
                normalized_value=None,
                page_number=page_number,
                quote=quote,
                bbox=bbox,
                reliable=False,
                reason=unreadable_reason,
                source="native_text",
            )

        hits = finder(doc)
        if not hits:
            return ExtractedIdentity(
                field_kind=field_kind,
                field_label=field_label,
                raw_value=None,
                normalized_value=None,
                page_number=None,
                quote=None,
                bbox=None,
                reliable=False,
                reason=missing_reason,
                source="native_text",
            )

        normalized_values = {item[1] for item in hits}
        if len(normalized_values) > 1:
            page_number, _normalized, raw, quote, bbox = hits[0]
            return ExtractedIdentity(
                field_kind=field_kind,
                field_label=field_label,
                raw_value=raw,
                normalized_value=None,
                page_number=page_number,
                quote=quote,
                bbox=bbox,
                reliable=False,
                reason=f"同一材料中出现多个不一致的{field_label}，需人工确认",
                source="native_text",
            )

        page_number, normalized, raw, quote, bbox = hits[0]
        return ExtractedIdentity(
            field_kind=field_kind,
            field_label=field_label,
            raw_value=raw,
            normalized_value=normalized,
            page_number=page_number,
            quote=quote,
            bbox=bbox,
            reliable=True,
            reason=None,
            source="native_text",
        )
    finally:
        doc.close()


def _first_marker_hit(
    doc: pymupdf.Document,
    markers: tuple[str, ...],
) -> tuple[int, str, BBox | None] | None:
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        text = page.get_text() or ""
        for marker in markers:
            if marker not in text:
                continue
            bbox = _locate_bbox(page, [marker])
            quote = _quote_containing(text, marker)
            return page_index + 1, quote, bbox
    return None


def _find_project_name(
    doc: pymupdf.Document,
) -> list[tuple[int, str, str, str, BBox | None]]:
    hits: list[tuple[int, str, str, str, BBox | None]] = []
    seen: set[str] = set()
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        lines = _nonempty_lines(page.get_text() or "")
        labeled = _value_after_label(lines, "项目名称")
        heading = _heading_title(lines)
        candidates: list[str] = []
        if labeled:
            candidates.append(labeled)
        if heading and heading not in candidates:
            candidates.append(heading)
        for raw in candidates:
            normalized = normalize_project_name(raw)
            if not normalized or normalized in _NOT_PROJECT_NAMES:
                continue
            if _too_short_title(normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            quote = f"项目名称 {raw.replace(chr(10), '')}".strip()
            bbox = _locate_bbox(page, _title_needles(raw, normalized))
            hits.append((page_number, normalized, raw, quote, bbox))
    return hits


def _find_principal(
    doc: pymupdf.Document,
) -> list[tuple[int, str, str, str, BBox | None]]:
    hits: list[tuple[int, str, str, str, BBox | None]] = []
    seen: set[str] = set()
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        lines = _nonempty_lines(page.get_text() or "")
        for raw in _principal_values(lines):
            normalized = normalize_person_name(raw)
            if not _looks_like_person_name(normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            quote = f"项目负责人 {raw}".strip()
            bbox = _locate_bbox(page, [raw, normalized, "项目负责人"])
            hits.append((page_number, normalized, raw, quote, bbox))
    return hits


def _principal_values(lines: list[str]) -> list[str]:
    values: list[str] = []
    for index, line in enumerate(lines):
        if _is_unreadable_principal_line(line):
            continue
        if line.startswith("项目负责人声明") or line.endswith("项目负责人声明"):
            continue
        if line == "项目负责人":
            if index + 1 < len(lines):
                candidate = _take_person_name(lines[index + 1])
                if candidate:
                    values.append(candidate)
            continue
        match = _PI_INLINE.match(line)
        if match:
            candidate = _take_person_name(match.group(1).strip())
            if candidate:
                values.append(candidate)
    return values


def _value_after_label(lines: list[str], label: str) -> str | None:
    for index, line in enumerate(lines):
        if _is_annotated_label(line, label):
            continue
        if line == label:
            collected: list[str] = []
            for nxt in lines[index + 1 :]:
                if _is_field_label(nxt):
                    break
                if nxt in _NOT_PROJECT_NAMES:
                    continue
                collected.append(nxt)
            if collected:
                return "\n".join(collected)
            return None
        match = _TITLE_INLINE.match(line) if label == "项目名称" else None
        if match:
            value = match.group(1).strip()
            return value or None
    return None


def _heading_title(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if not _FILE_HEADING.match(line):
            continue
        collected: list[str] = []
        for nxt in lines[index + 1 :]:
            if _is_field_label(nxt):
                break
            if _is_annotated_label(nxt, "项目名称"):
                break
            if nxt in _NOT_PROJECT_NAMES:
                continue
            collected.append(nxt)
        if collected:
            return "\n".join(collected)
    return None


def _is_field_label(line: str) -> bool:
    stripped = line.strip()
    for label in _FIELD_LABELS:
        if stripped == label:
            return True
        if stripped.startswith(f"{label}：") or stripped.startswith(f"{label}:"):
            return True
        if stripped.startswith(f"{label}（") or stripped.startswith(f"{label}("):
            return True
    return False


def _is_annotated_label(line: str, label: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(f"{label}（") or stripped.startswith(f"{label}(")


def _is_unreadable_principal_line(line: str) -> bool:
    if not line.startswith("项目负责人"):
        return False
    return any(marker in line for marker in ("手写", "原生文本不含", "无法区分"))


def _too_short_title(normalized: str) -> bool:
    cjk = re.findall(r"[\u4e00-\u9fff]", normalized)
    return len(cjk) < 6


def _looks_like_person_name(value: str) -> bool:
    return bool(_PERSON_NAME.match(value))


def _take_person_name(value: str) -> str | None:
    cleaned = normalize_person_name(value)
    if _looks_like_person_name(cleaned):
        return cleaned
    match = _PERSON_NAME_PREFIX.match(cleaned)
    if match and _looks_like_person_name(match.group(1)):
        return match.group(1)
    return None


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _title_needles(raw: str, normalized: str) -> list[str]:
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    needles = [raw.replace("\n", ""), first_line, normalized]
    if len(first_line) > 8:
        needles.append(first_line[:8])
    return needles


def _locate_bbox(page: pymupdf.Page, needles: list[str]) -> BBox | None:
    for needle in needles:
        if not needle:
            continue
        hits = page.search_for(needle)
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


def _quote_containing(text: str, marker: str) -> str:
    for line in _nonempty_lines(text):
        if marker in line:
            return line
    return marker
