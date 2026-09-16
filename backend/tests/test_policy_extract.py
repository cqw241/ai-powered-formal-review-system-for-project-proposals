"""Unit tests for deterministic policy candidate extraction."""

from __future__ import annotations

from pathlib import Path

from app.services.policy_extract import (
    extract_candidates_from_pdf,
    extract_funding_cap_candidates,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"

SAMPLE_TEXT = """
2026青年科研创新培育计划申报指南（开发批次规范）
P-04 项目周期。 项目执行期不得早于2027-01-01开始。
P-05 申请经费上限。 自然科学类申请经费不超过30 万元；人文社会科学类不超过15 万元。币种统一为人民币。
P-06 预算合计一致。 预算表各科目金额之和必须等于预算表申请总额。
"""


def test_funding_cap_from_sample_text():
    pages = [(1, SAMPLE_TEXT)]
    caps = extract_funding_cap_candidates(SAMPLE_TEXT, pages)
    assert len(caps) == 2
    by_cat = {item.category: item for item in caps}
    natural = by_cat["自然科学类"]
    social = by_cat["人文社会科学类"]
    assert natural.amount_yuan == 300_000
    assert natural.amount_unit == "万元"
    assert natural.comparator == "LE"
    assert natural.source_clause == "P-05"
    assert natural.source_page == 1
    assert "不超过" in (natural.source_quote or "")
    assert social.amount_yuan == 150_000
    assert social.amount_unit == "万元"
    assert social.source_clause == "P-05"


def test_funding_cap_without_clause_marker():
    text = "自然科学类申请经费不超过30万元；人文社会科学类不超过15万元。"
    pages = [(1, text)]
    caps = extract_funding_cap_candidates(text, pages)
    assert len(caps) == 2
    assert all(item.source_clause is None for item in caps)
    assert {item.category: item.amount_yuan for item in caps} == {
        "自然科学类": 300_000,
        "人文社会科学类": 150_000,
    }


def test_unknown_amount_not_zero():
    text = "P-05 申请经费上限。 自然科学类申请经费不超过 待定；人文社会科学类不超过15万元。"
    pages = [(1, text)]
    caps = extract_funding_cap_candidates(text, pages)
    by_cat = {item.category: item for item in caps}
    # Natural sciences pattern requires a numeric amount; if unmatched, absent.
    # Social sciences still parses.
    assert "人文社会科学类" in by_cat
    assert by_cat["人文社会科学类"].amount_yuan == 150_000
    if "自然科学类" in by_cat:
        assert by_cat["自然科学类"].amount_yuan is None


def test_extract_from_fixture_pdf():
    assert POLICY_PDF.is_file()
    candidates = extract_candidates_from_pdf(POLICY_PDF)
    funding = [c for c in candidates if c.kind == "FUNDING_CAP"]
    assert len(funding) == 2
    by_cat = {c.category: c for c in funding}
    assert by_cat["自然科学类"].amount_yuan == 300_000
    assert by_cat["人文社会科学类"].amount_yuan == 150_000
    assert by_cat["自然科学类"].source_page == 1
    assert any(c.source_clause == "P-01" for c in candidates)
    assert any(c.source_clause == "P-10" for c in candidates)
    # P-05 itself is represented only via FUNDING_CAP rows.
    assert not any(c.kind == "CLAUSE" and c.source_clause == "P-05" for c in candidates)
