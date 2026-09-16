"""RULE-002/003 identity consistency: PASS / FAIL / NEED_HUMAN_REVIEW."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base
from app.models import Material, MaterialCategory, MaterialStatus, Project
from app.review_contract import ReviewCheckStatus, RuleExecutionContext
from app.services.identity_extract import (
    extract_principal_from_pdf,
    extract_project_name_from_pdf,
    normalize_project_name,
)
from app.services.identity_review import execute_identity_rule
from app.services.pdf import inspect_pdf
from app.services.storage import material_file_path, relative_storage_key

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"

A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"
A3 = FIXTURES / "A3_科研诚信与合规承诺书.pdf"
A2_PI_VARIANT = FIXTURES / "A2_经费预算表_负责人林书彦.pdf"

B1 = FIXTURES / "B1_项目申报书.pdf"
B2 = FIXTURES / "B2_经费预算表.pdf"
B3 = FIXTURES / "B3_科研诚信与合规承诺书.pdf"
B2_BLURRED = FIXTURES / "B2_经费预算表_标题模糊.pdf"
B3_HANDWRITTEN = FIXTURES / "B3_承诺书_手写负责人.pdf"

C1 = FIXTURES / "C1_项目申报书.pdf"
C2 = FIXTURES / "C2_经费预算表.pdf"
C3 = FIXTURES / "C3_科研诚信与合规承诺书.pdf"

PKG_A_NAME = "面向低功耗边缘计算的自适应任务调度方法研究"
PKG_B_NAME = "生成式人工智能辅助大学生学术写作的认知负荷与反馈机制研究"
PKG_B_COMMITMENT_NAME = "生成式人工智能辅助大学生学术写作反馈机制研究"
PKG_C_NAME = "面向脑机交互信号的轻量级自监督表征学习研究"


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    materials_path = tmp_path / "materials"
    materials_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MATERIALS_DIR", str(materials_path))
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    from app import models  # noqa: F401
    from app.services import review_results  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        get_settings.cache_clear()


def _write_pdf(path: Path, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = pymupdf.Font("china-s")
    writer = pymupdf.TextWriter(page.rect)
    y = 72
    for line in lines:
        writer.append((72, y), line, font=font, fontsize=12)
        y += 24
    writer.write_text(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _seed_project(db: Session, files: list[tuple[Path, str]]) -> str:
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="identity-test"))
    db.flush()
    for path, category in files:
        material_id = str(uuid.uuid4())
        dest = material_file_path(material_id)
        shutil.copy2(path, dest)
        db.add(
            Material(
                id=material_id,
                project_id=project_id,
                original_filename=path.name,
                category=category,
                page_count=inspect_pdf(dest),
                status=MaterialStatus.READY.value,
                storage_path=relative_storage_key(material_id),
            )
        )
    db.commit()
    return project_id


def _context(project_id: str, rule_code: str) -> RuleExecutionContext:
    return RuleExecutionContext(
        project_id=project_id,
        review_item_id=str(uuid.uuid4()),
        rule_code=rule_code,
    )


def _run(db: Session, project_id: str, rule_code: str):
    return execute_identity_rule(db, _context(project_id, rule_code))


def test_normalize_project_name_collapses_unicode_and_cjk_wrap():
    wrapped = "面向低功耗边缘计算的自适应任务调度\n方法研究"
    spaced = "面向低功耗边缘计算的自适应任务调度  方法研究"
    ideographic = "面向低功耗边缘计算的自适应任务调度\u3000方法研究"
    assert normalize_project_name(wrapped) == PKG_A_NAME
    assert normalize_project_name(spaced) == PKG_A_NAME
    assert normalize_project_name(ideographic) == PKG_A_NAME
    assert normalize_project_name(f"  {PKG_B_NAME}  ") == PKG_B_NAME
    assert normalize_project_name(PKG_B_COMMITMENT_NAME) != PKG_B_NAME


def test_extract_pkg_a_commitment_wraps_to_same_title():
    application = extract_project_name_from_pdf(A1)
    commitment = extract_project_name_from_pdf(A3)
    assert application.reliable and commitment.reliable
    assert application.normalized_value == PKG_A_NAME
    assert commitment.normalized_value == PKG_A_NAME
    assert commitment.page_number == 1
    assert commitment.quote


def test_rule_002_pkg_a_pass_after_whitespace_normalization(db: Session):
    project_id = _seed_project(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-002")

    assert result.status == ReviewCheckStatus.PASS
    assert PKG_A_NAME in result.summary
    assert {item.category for item in result.evidence} == {
        "APPLICATION",
        "BUDGET",
        "COMMITMENT",
    }
    assert all(item.reliable for item in result.evidence)
    assert all(item.normalized_value == PKG_A_NAME for item in result.evidence)
    application = next(item for item in result.evidence if item.category == "APPLICATION")
    assert application.material_id
    assert application.page_number == 1
    assert application.quote
    assert application.bbox is not None
    assert application.field_name == "项目名称"


def test_rule_002_pkg_b_fail_lists_both_titles(db: Session):
    project_id = _seed_project(
        db,
        [
            (B1, MaterialCategory.APPLICATION.value),
            (B2, MaterialCategory.BUDGET.value),
            (B3, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-002")

    assert result.status == ReviewCheckStatus.FAIL
    assert PKG_B_NAME in result.summary
    assert PKG_B_COMMITMENT_NAME in result.summary
    names = {item.normalized_value for item in result.evidence if item.reliable}
    assert names == {PKG_B_NAME, PKG_B_COMMITMENT_NAME}
    commitment = next(item for item in result.evidence if item.category == "COMMITMENT")
    assert commitment.raw_value
    assert "认知负荷与" not in (commitment.normalized_value or "")
    assert commitment.page_number == 1
    assert commitment.quote
    assert commitment.material_id


def test_rule_002_blurred_title_need_human(db: Session, tmp_path: Path):
    commitment = tmp_path / "commitment_matching.pdf"
    _write_pdf(
        commitment,
        [
            "科研诚信与合规承诺书",
            "项目名称",
            PKG_B_NAME,
            "项目负责人",
            "周清岚",
        ],
    )
    project_id = _seed_project(
        db,
        [
            (B1, MaterialCategory.APPLICATION.value),
            (B2_BLURRED, MaterialCategory.BUDGET.value),
            (commitment, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-002")

    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    budget = next(item for item in result.evidence if item.category == "BUDGET")
    assert budget.reliable is False
    assert budget.normalized_value is None
    assert budget.reason
    assert "人工" in result.summary or "稳定" in result.summary or "识别" in result.summary


def test_rule_002_whitespace_only_synthetic_names_pass(db: Session, tmp_path: Path):
    application = tmp_path / "app.pdf"
    budget = tmp_path / "budget.pdf"
    commitment = tmp_path / "commit.pdf"
    _write_pdf(
        application,
        [
            "文件 T1  项目申报书",
            PKG_A_NAME,
            "项目负责人",
            "林书言",
        ],
    )
    _write_pdf(
        budget,
        [
            "文件 T2  经费预算表",
            "面向低功耗边缘计算的自适应任务调度  方法研究",
            "项目负责人",
            "林书言",
            "申请总额",
            "300000元",
        ],
    )
    _write_pdf(
        commitment,
        [
            "科研诚信与合规承诺书",
            "项目名称",
            "面向低功耗边缘计算的自适应任务调度",
            "方法研究",
            "项目负责人",
            "林书言",
        ],
    )
    project_id = _seed_project(
        db,
        [
            (application, MaterialCategory.APPLICATION.value),
            (budget, MaterialCategory.BUDGET.value),
            (commitment, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-002")
    assert result.status == ReviewCheckStatus.PASS
    assert all(item.normalized_value == PKG_A_NAME for item in result.evidence)


def test_rule_003_pkg_c_pass_same_principal(db: Session):
    project_id = _seed_project(
        db,
        [
            (C1, MaterialCategory.APPLICATION.value),
            (C2, MaterialCategory.BUDGET.value),
            (C3, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-003")

    assert result.status == ReviewCheckStatus.PASS
    assert "顾言澈" in result.summary
    assert all(item.normalized_value == "顾言澈" for item in result.evidence)
    assert all(item.reliable for item in result.evidence)
    assert all(item.field_name == "项目负责人" for item in result.evidence)
    application = next(item for item in result.evidence if item.category == "APPLICATION")
    assert application.page_number == 1
    assert application.quote
    assert application.bbox is not None
    assert application.material_id


def test_rule_003_principal_conflict_fail(db: Session):
    project_id = _seed_project(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2_PI_VARIANT, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-003")

    assert result.status == ReviewCheckStatus.FAIL
    assert "林书言" in result.summary
    assert "林书彦" in result.summary
    names = {item.normalized_value for item in result.evidence if item.reliable}
    assert names == {"林书言", "林书彦"}
    budget = next(item for item in result.evidence if item.category == "BUDGET")
    assert budget.raw_value == "林书彦"
    assert budget.page_number == 1
    assert budget.quote
    assert budget.material_id


def test_rule_003_handwritten_principal_need_human(db: Session):
    project_id = _seed_project(
        db,
        [
            (B1, MaterialCategory.APPLICATION.value),
            (B2, MaterialCategory.BUDGET.value),
            (B3_HANDWRITTEN, MaterialCategory.COMMITMENT.value),
        ],
    )
    result = _run(db, project_id, "RULE-003")

    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    commitment = next(item for item in result.evidence if item.category == "COMMITMENT")
    assert commitment.reliable is False
    assert commitment.normalized_value is None
    assert commitment.reason
    assert extract_principal_from_pdf(B3_HANDWRITTEN).reliable is False


def test_missing_materials_need_human_not_fail(db: Session):
    project_id = _seed_project(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
        ],
    )
    name_result = _run(db, project_id, "RULE-002")
    principal_result = _run(db, project_id, "RULE-003")

    assert name_result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert principal_result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert name_result.status != ReviewCheckStatus.FAIL
    assert "缺少" in name_result.summary
    commitment = next(item for item in name_result.evidence if item.category == "COMMITMENT")
    assert commitment.reliable is False
    assert commitment.material_id is None


def test_unknown_rule_code_is_system_error(db: Session):
    project_id = _seed_project(db, [(A1, MaterialCategory.APPLICATION.value)])
    result = _run(db, project_id, "RULE-007")
    assert result.status == ReviewCheckStatus.SYSTEM_ERROR
    assert "RULE-007" in result.summary
