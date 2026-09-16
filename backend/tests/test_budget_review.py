"""B08 RULE-005/006: category cap, budget-subject sum, 1-yuan tolerance, HUMAN bounds."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base
from app.models import Material, MaterialCategory, MaterialStatus, Project
from app.review_contract import ReviewCheckStatus, RuleExecutionContext
from app.services.budget_extract import (
    HUMANITIES,
    NATURAL_SCIENCE,
    extract_budget,
    extract_project_category,
)
from app.services.budget_review import execute_budget_rule
from app.services.money import difference_yuan

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
A1 = FIXTURES / "A1_项目申报书.pdf"
A1_NO_CATEGORY = FIXTURES / "A1_项目申报书_类别缺失.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"
A2_DIFF_2 = FIXTURES / "A2_经费预算表_合计差额2元.pdf"
A2_SCOPE = FIXTURES / "A2_经费预算表_配套经费口径不清.pdf"
B1 = FIXTURES / "B1_项目申报书.pdf"
B2 = FIXTURES / "B2_经费预算表.pdf"
C1 = FIXTURES / "C1_项目申报书.pdf"
C2 = FIXTURES / "C2_经费预算表.pdf"


def _natural_snapshot(**overrides):
    snapshot = {
        "rule_id": "rule-natural",
        "version_id": "ver-natural",
        "version_number": 1,
        "rule_code": "RULE-005",
        "name": "分类申请经费上限（自然科学类）",
        "category": NATURAL_SCIENCE,
        "compare_field": "申请经费",
        "comparator": "LE",
        "amount_yuan": 300_000,
        "amount_raw": "30万元",
        "source_clause": "P-05",
        "application_amount_yuan": None,
    }
    snapshot.update(overrides)
    return snapshot


def _humanities_snapshot(**overrides):
    snapshot = {
        "rule_id": "rule-humanities",
        "version_id": "ver-humanities",
        "version_number": 1,
        "rule_code": "RULE-005",
        "name": "分类申请经费上限（人文社会科学类）",
        "category": HUMANITIES,
        "compare_field": "申请经费",
        "comparator": "LE",
        "amount_yuan": 150_000,
        "amount_raw": "15万元",
        "source_clause": "P-05",
        "application_amount_yuan": None,
    }
    snapshot.update(overrides)
    return snapshot


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MATERIALS_DIR", str(materials_dir))
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return Session(engine)


def _add_project(db: Session, name: str = "B08") -> str:
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name=name))
    db.commit()
    return project_id


def _add_material(
    db: Session,
    project_id: str,
    path: Path,
    category: MaterialCategory,
) -> Material:
    material_id = str(uuid.uuid4())
    dest = get_settings().resolve_materials_dir() / f"{material_id}.pdf"
    shutil.copy2(path, dest)
    material = Material(
        id=material_id,
        project_id=project_id,
        original_filename=path.name,
        category=category.value,
        page_count=1,
        status=MaterialStatus.READY.value,
        storage_path=f"{material_id}.pdf",
    )
    db.add(material)
    db.commit()
    return material


def _seed(
    db: Session,
    *,
    application: Path | None = None,
    budget: Path | None = None,
    name: str = "B08",
) -> str:
    project_id = _add_project(db, name)
    if application is not None:
        _add_material(db, project_id, application, MaterialCategory.APPLICATION)
    if budget is not None:
        _add_material(db, project_id, budget, MaterialCategory.BUDGET)
    return project_id


def _context(project_id: str, rule_code: str, snapshot: dict | None = None) -> RuleExecutionContext:
    return RuleExecutionContext(
        project_id=project_id,
        review_item_id=str(uuid.uuid4()),
        rule_code=rule_code,
        source_rule_id=None if snapshot is None else snapshot.get("rule_id"),
        version_id=None if snapshot is None else snapshot.get("version_id"),
        version_number=None if snapshot is None else snapshot.get("version_number"),
        snapshot=snapshot,
    )


def _write_pdf(path: Path, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = pymupdf.Font("china-s")
    writer = pymupdf.TextWriter(page.rect)
    y = 72
    for line in lines:
        writer.append((72, y), line, font=font, fontsize=12)
        y += 22
    writer.write_text(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


# --- extract: category -------------------------------------------------------


def test_extract_natural_science_category_from_pkg_a():
    result = extract_project_category(A1)
    assert result.reliable is True
    assert result.normalized == NATURAL_SCIENCE
    assert result.raw_value and "自然科学" in result.raw_value
    assert result.page_number == 1
    assert result.bbox is not None


def test_extract_humanities_category_from_pkg_b():
    result = extract_project_category(B1)
    assert result.reliable is True
    assert result.normalized == HUMANITIES
    assert result.page_number == 1


def test_extract_missing_category_is_unreliable_not_zero():
    result = extract_project_category(A1_NO_CATEGORY)
    assert result.reliable is False
    assert result.normalized is None
    assert "类别" in (result.reason or "")


# --- extract: budget lines ---------------------------------------------------


def test_extract_pkg_c_subject_sum_equals_286000():
    budget = extract_budget(C2)
    assert budget.application_total.reliable
    assert budget.application_total.amount_yuan == 286_000
    assert budget.line_sum_yuan == 286_000
    assert budget.scope_uncertain is False
    names = [item.name for item in budget.detail_lines]
    assert "设备费" in names
    assert "合计" not in names
    assert all(item.reliable and item.amount_yuan is not None for item in budget.detail_lines)


def test_extract_pkg_a_subject_sum_equals_300000_and_skips_breakdown_tables():
    budget = extract_budget(A2)
    assert budget.application_total.amount_yuan == 300_000
    assert budget.line_sum_yuan == 300_000
    names = [item.name for item in budget.detail_lines]
    assert names == [
        "设备费",
        "材料费",
        "测试化验加工费",
        "差旅/会议/交流费",
        "出版/文献/信息传播费",
        "劳务费",
    ]


def test_extract_sum_difference_two_yuan():
    budget = extract_budget(A2_DIFF_2)
    assert budget.application_total.amount_yuan == 150_000
    assert budget.line_sum_yuan == 149_998
    assert difference_yuan(budget.line_sum_yuan, budget.application_total.amount_yuan) == 2


def test_extract_matching_funding_scope_is_uncertain():
    budget = extract_budget(A2_SCOPE)
    assert budget.scope_uncertain is True
    assert budget.application_total.amount_yuan == 300_000
    assert "配套" in (budget.scope_reason or "") or "口径" in (budget.scope_reason or "")


# --- RULE-005 ----------------------------------------------------------------


def test_rule_005_pkg_a_at_natural_science_cap_passes(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2, name="CASE-013")
    result = execute_budget_rule(db, _context(project_id, "RULE-005", _natural_snapshot()))
    assert result.status == ReviewCheckStatus.PASS
    assert result.data["application_amount_yuan"] == 300_000
    assert result.data["cap_yuan"] == 300_000
    assert "300000" in result.summary and "上限" in result.summary
    assert any(item.field_name == "项目类别" and item.reliable for item in result.evidence)
    db.close()


def test_rule_005_pkg_b_humanities_153000_exceeds_150000(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=B1, budget=B2, name="CASE-014")
    result = execute_budget_rule(db, _context(project_id, "RULE-005", _humanities_snapshot()))
    assert result.status == ReviewCheckStatus.FAIL
    assert result.data["application_amount_yuan"] == 153_000
    assert result.data["cap_yuan"] == 150_000
    assert result.data["difference_yuan"] == 3_000
    assert "153000" in result.summary
    assert "150000" in result.summary
    db.close()


def test_rule_005_missing_category_need_human_review(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1_NO_CATEGORY, budget=A2, name="CASE-015")
    result = execute_budget_rule(db, _context(project_id, "RULE-005", _natural_snapshot()))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "项目类别" in result.summary
    assert "项目类别" in result.data["pending_fields"]
    assert result.data.get("project_category") in (None, "")
    assert result.data.get("application_amount_yuan") is None
    db.close()


def test_rule_005_uses_bound_snapshot_cap_not_hardcoded_policy(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2)
    snapshot = _natural_snapshot(amount_yuan=280_000, amount_raw="28万元", version_number=2)
    result = execute_budget_rule(db, _context(project_id, "RULE-005", snapshot))
    assert result.status == ReviewCheckStatus.FAIL
    assert result.data["cap_yuan"] == 280_000
    assert result.data["application_amount_yuan"] == 300_000
    assert "280000" in result.summary
    db.close()


def test_rule_005_snapshot_category_mismatch_need_human(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=B1, budget=B2)
    result = execute_budget_rule(db, _context(project_id, "RULE-005", _natural_snapshot()))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "不一致" in result.summary
    db.close()


def test_rule_005_missing_application_need_human_not_fail(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, budget=A2)
    result = execute_budget_rule(db, _context(project_id, "RULE-005", _natural_snapshot()))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "申报书" in result.summary or "项目类别" in result.summary
    db.close()


def test_rule_005_unknown_cap_amount_is_not_zero(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2)
    snapshot = _natural_snapshot(amount_yuan=None, amount_raw=None)
    result = execute_budget_rule(db, _context(project_id, "RULE-005", snapshot))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "上限" in result.summary
    db.close()


# --- RULE-006 ----------------------------------------------------------------


def test_rule_006_pkg_c_subject_sum_matches_application_total(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=C1, budget=C2, name="CASE-016")
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.PASS
    assert result.data["line_sum_yuan"] == 286_000
    assert result.data["application_total_yuan"] == 286_000
    assert result.data["difference_yuan"] == 0
    assert "286000" in result.summary
    assert "一致" in result.summary
    db.close()


def test_rule_006_difference_over_one_yuan_fails_with_delta(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2_DIFF_2, name="CASE-017")
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.FAIL
    assert result.data["line_sum_yuan"] == 149_998
    assert result.data["application_total_yuan"] == 150_000
    assert result.data["difference_yuan"] == 2
    assert "差额 2 元" in result.summary or "合计差额 2 元" in result.summary
    db.close()


def test_rule_006_one_yuan_difference_still_passes(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    path = tmp_path / "sum_off_by_one.pdf"
    _write_pdf(
        path,
        [
            "经费预算表",
            "申请总额 150000 元",
            "一、科目预算",
            "科目",
            "金额（元）",
            "设备费",
            "100,000",
            "材料费",
            "49,999",
            "合计",
            "149,999",
        ],
    )
    project_id = _seed(db, budget=path, name="tolerance-1")
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.PASS
    assert result.data["difference_yuan"] == 1
    assert result.data["line_sum_yuan"] == 149_999
    db.close()


def test_rule_006_uncertain_matching_funding_need_human(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2_SCOPE, name="CASE-018")
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "配套" in result.summary or "口径" in result.summary
    assert "配套经费" in result.data["pending_fields"]
    db.close()


def test_rule_006_missing_budget_need_human_not_fail(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1)
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "预算" in result.summary
    db.close()


def test_rule_006_unreadable_subjects_need_human(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    path = tmp_path / "no_table.pdf"
    _write_pdf(path, ["经费预算表", "申请总额 150000 元", "科目金额见附件，本页无明细"])
    project_id = _seed(db, budget=path)
    result = execute_budget_rule(db, _context(project_id, "RULE-006"))
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.data.get("line_sum_yuan") is None
    db.close()


def test_unsupported_rule_code_is_system_error(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project_id = _seed(db, application=A1, budget=A2)
    result = execute_budget_rule(db, _context(project_id, "RULE-007"))
    assert result.status == ReviewCheckStatus.SYSTEM_ERROR
    db.close()
