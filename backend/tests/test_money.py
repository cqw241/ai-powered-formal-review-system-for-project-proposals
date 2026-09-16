"""Unit conversion and amount parsing tests."""

from __future__ import annotations

from app.services.money import difference_yuan, parse_amount_text


def test_parse_wan_yuan_and_yuan_normalize_equal():
    left = parse_amount_text("30万元")
    right = parse_amount_text("300000元")
    assert left is not None and right is not None
    assert left.amount_yuan == 300_000
    assert right.amount_yuan == 300_000
    assert difference_yuan(left.amount_yuan, right.amount_yuan) == 0


def test_parse_decimal_wan_yuan():
    parsed = parse_amount_text("30.00 万元")
    assert parsed is not None
    assert parsed.amount_yuan == 300_000
    assert parsed.unit == "wan_yuan"


def test_parse_thousand_yuan():
    parsed = parse_amount_text("300 千元")
    assert parsed is not None
    assert parsed.amount_yuan == 300_000


def test_parse_comma_grouped_yuan():
    parsed = parse_amount_text("300,000 元")
    assert parsed is not None
    assert parsed.amount_yuan == 300_000


def test_unknown_unit_is_not_zero():
    parsed = parse_amount_text("300000")
    assert parsed is None
    assert difference_yuan(None, 0) is None
    assert difference_yuan(None, None) is None


def test_difference_for_320000_vs_300000():
    left = parse_amount_text("30.00万元")
    right = parse_amount_text("320000元")
    assert left is not None and right is not None
    assert difference_yuan(left.amount_yuan, right.amount_yuan) == 20_000
