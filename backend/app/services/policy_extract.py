"""Deterministic candidate-requirement extraction from policy PDF text.

Candidates are editable drafts — not enabled rules (B05). Amount arithmetic
stays in the money service; unknown amounts remain None and are never coerced to 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.services import pdf as pdf_service
from app.services.money import UNIT_LABELS, parse_amount_text

# Collapse whitespace so PDF line breaks ("30 万元") still match.
_WS = re.compile(r"\s+")

# Clause markers like P-05 / P-10 at line or sentence starts.
_CLAUSE_SPLIT = re.compile(r"(?=P-\d+\s)")

# Funding-cap patterns tied to category labels in this batch's guide (P-05).
_FUNDING_CAP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "自然科学类",
        re.compile(
            r"自然科学类[^。；;]{0,40}?不超过\s*(?P<amount>\d+(?:\.\d+)?\s*万?\s*元)",
            re.IGNORECASE,
        ),
    ),
    (
        "人文社会科学类",
        re.compile(
            r"人文社会科学类[^。；;]{0,40}?不超过\s*(?P<amount>\d+(?:\.\d+)?\s*万?\s*元)",
            re.IGNORECASE,
        ),
    ),
)

_CLAUSE_ID_RE = re.compile(r"^(P-\d+)\s*(.*)$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    """One draft requirement extracted from policy text."""

    kind: str
    title: str
    category: str | None
    amount_raw: str | None
    amount_yuan: int | None
    amount_unit: str | None
    comparator: str | None
    source_clause: str | None
    source_page: int | None
    source_quote: str | None
    sort_order: int


def _norm(text: str) -> str:
    return _WS.sub(" ", text.replace("\u3000", " ")).strip()


def _page_texts(path: Path) -> list[tuple[int, str]]:
    page_count = pdf_service.inspect_pdf(path)
    pages: list[tuple[int, str]] = []
    for page_number in range(1, page_count + 1):
        pages.append((page_number, pdf_service.get_page_text(path, page_number) or ""))
    return pages


def _find_page_for_snippet(pages: list[tuple[int, str]], snippet: str) -> int | None:
    needle = _norm(snippet)
    if not needle:
        return None
    for page_number, text in pages:
        if needle[:40] in _norm(text):
            return page_number
    # Fallback: shorter unique token
    token = needle[:20]
    for page_number, text in pages:
        if token and token in _norm(text):
            return page_number
    return pages[0][0] if pages else None


def _parse_amount_fields(amount_raw: str | None) -> tuple[str | None, int | None, str | None]:
    if not amount_raw:
        return None, None, None
    cleaned = _norm(amount_raw)
    parsed = parse_amount_text(cleaned)
    if parsed is None:
        # Retry after removing internal spaces between digits and 万/元.
        compact = cleaned.replace(" ", "")
        parsed = parse_amount_text(compact)
    if parsed is None:
        return cleaned, None, None
    display_unit = UNIT_LABELS.get(parsed.unit)
    return cleaned, parsed.amount_yuan, display_unit


def extract_funding_cap_candidates(full_text: str, pages: list[tuple[int, str]]) -> list[ExtractedCandidate]:
    """Extract category-specific funding upper bounds (e.g. P-05)."""
    normalized = _norm(full_text)
    clause_id = "P-05" if "P-05" in normalized else None
    # Prefer the sentence / clause containing P-05 when present.
    quote_region = normalized
    if clause_id:
        for chunk in _CLAUSE_SPLIT.split(normalized):
            chunk_n = _norm(chunk)
            if chunk_n.startswith("P-05"):
                quote_region = chunk_n
                break

    results: list[ExtractedCandidate] = []
    sort_base = 50
    for offset, (category, pattern) in enumerate(_FUNDING_CAP_PATTERNS):
        match = pattern.search(quote_region) or pattern.search(normalized)
        if match is None:
            continue
        amount_raw, amount_yuan, amount_unit = _parse_amount_fields(match.group("amount"))
        quote = _norm(match.group(0))
        page = _find_page_for_snippet(pages, quote)
        if page is None and clause_id:
            page = _find_page_for_snippet(pages, clause_id)
        results.append(
            ExtractedCandidate(
                kind="FUNDING_CAP",
                title="申请经费上限",
                category=category,
                amount_raw=amount_raw,
                amount_yuan=amount_yuan,
                amount_unit=amount_unit,
                comparator="LE",
                source_clause=clause_id,
                source_page=page,
                source_quote=quote,
                sort_order=sort_base + offset,
            )
        )
    return results


def extract_clause_candidates(full_text: str, pages: list[tuple[int, str]]) -> list[ExtractedCandidate]:
    """Extract P-XX clauses as generic draft requirements (non-funding detail)."""
    normalized = _norm(full_text)
    chunks = [c for c in _CLAUSE_SPLIT.split(normalized) if _norm(c)]
    results: list[ExtractedCandidate] = []
    order = 100
    for chunk in chunks:
        text = _norm(chunk)
        matched = _CLAUSE_ID_RE.match(text)
        if matched is None:
            continue
        clause_id = matched.group(1)
        body = _norm(matched.group(2))
        # Funding caps are represented by dedicated FUNDING_CAP rows.
        if clause_id == "P-05":
            continue
        # Title: first short sentence fragment before 。 or ；
        title_part = re.split(r"[。；;]", body, maxsplit=1)[0].strip(" .")
        if len(title_part) > 40:
            title_part = title_part[:40].rstrip() + "…"
        title = title_part or clause_id
        quote = text if len(text) <= 180 else text[:177].rstrip() + "…"
        page = _find_page_for_snippet(pages, clause_id) or _find_page_for_snippet(pages, quote[:30])
        results.append(
            ExtractedCandidate(
                kind="CLAUSE",
                title=title,
                category=None,
                amount_raw=None,
                amount_yuan=None,
                amount_unit=None,
                comparator=None,
                source_clause=clause_id,
                source_page=page,
                source_quote=quote,
                sort_order=order,
            )
        )
        order += 1
    return results


def extract_candidates_from_pdf(path: Path) -> list[ExtractedCandidate]:
    """Read all pages and return ordered draft candidates."""
    pages = _page_texts(path)
    full_text = "\n".join(text for _, text in pages)
    funding = extract_funding_cap_candidates(full_text, pages)
    clauses = extract_clause_candidates(full_text, pages)
    combined = [*funding, *clauses]
    combined.sort(key=lambda item: (item.sort_order, item.source_clause or "", item.category or ""))
    return combined
