"""Amount parsing and unit normalization for funding comparison.

Business arithmetic lives here — never execute model-returned code.
Unknown / unparseable values stay None; they are never treated as zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

AmountUnit = Literal["yuan", "thousand_yuan", "wan_yuan"]

UNIT_TO_YUAN: dict[AmountUnit, Decimal] = {
    "yuan": Decimal("1"),
    "thousand_yuan": Decimal("1000"),
    "wan_yuan": Decimal("10000"),
}

UNIT_LABELS: dict[AmountUnit, str] = {
    "yuan": "元",
    "thousand_yuan": "千元",
    "wan_yuan": "万元",
}

_UNIT_ALIASES: dict[str, AmountUnit] = {
    "元": "yuan",
    "人民币元": "yuan",
    "rmb": "yuan",
    "cny": "yuan",
    "千元": "thousand_yuan",
    "万元": "wan_yuan",
    "万": "wan_yuan",
}

# Number + unit. Prefer plain digits / required comma-groups so "300000" is not
# truncated to "300". Unit may be omitted only when caller supplies default_unit.
_AMOUNT_RE = re.compile(
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>人民币元|万元|千元|万|元|RMB|CNY)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedAmount:
    """A reliably parsed monetary amount with its original unit."""

    raw_number: str
    raw_unit: str | None
    unit: AmountUnit
    amount_yuan: int  # rounded to nearest fen-free yuan (整元)


def normalize_unit_token(token: str | None) -> AmountUnit | None:
    if token is None:
        return None
    cleaned = token.strip().lower().replace(" ", "")
    if not cleaned:
        return None
    return _UNIT_ALIASES.get(cleaned) or _UNIT_ALIASES.get(token.strip())


def parse_amount_text(text: str, *, default_unit: AmountUnit | None = None) -> ParsedAmount | None:
    """Parse a short amount phrase such as ``30.00万元`` or ``300,000 元``.

    Returns None when the number or unit cannot be determined reliably.
    Does not invent zero for missing values.
    """
    if not text or not text.strip():
        return None

    cleaned = text.replace("\u3000", " ").strip()
    match = _AMOUNT_RE.search(cleaned)
    if match is None:
        return None

    # Prefer a match that includes an explicit unit when several exist.
    if match.group("unit") is None:
        for candidate in _AMOUNT_RE.finditer(cleaned):
            if candidate.group("unit"):
                match = candidate
                break

    number_raw = match.group("number")
    unit_raw = match.group("unit")
    unit = normalize_unit_token(unit_raw) if unit_raw else default_unit
    if unit is None:
        return None

    try:
        number = Decimal(number_raw.replace(",", ""))
    except InvalidOperation:
        return None

    yuan = (number * UNIT_TO_YUAN[unit]).quantize(Decimal("1"))
    return ParsedAmount(
        raw_number=number_raw,
        raw_unit=unit_raw,
        unit=unit,
        amount_yuan=int(yuan),
    )


def format_yuan(amount_yuan: int | None) -> str | None:
    if amount_yuan is None:
        return None
    return f"{amount_yuan} 元"


def difference_yuan(left: int | None, right: int | None) -> int | None:
    """Absolute difference when both sides are known; otherwise None."""
    if left is None or right is None:
        return None
    return abs(left - right)
