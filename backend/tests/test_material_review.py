"""B10 RULE-001/008/009: required files, equipment attachment, ethics gate."""

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
from app.services.material_extract import extract_equipment
from app.services.material_review import execute_material_rule
from app.services.pdf import inspect_pdf
from app.services.storage import material_file_path, relative_storage_key

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"

A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"
A3 = FIXTURES / "A3_科研诚信与合规承诺书.pdf"
A1_SUBJECTS = FIXTURES / "A1_项目申报书_招募受试者.pdf"

B1 = FIXTURES / "B1_项目申报书.pdf"
B2 = FIXTURES / "B2_经费预算表.pdf"
B3 = FIXTURES / "B3_科研诚信与合规承诺书.pdf"

C1 = FIXTURES / "C1_项目申报书.pdf"
C2 = FIXTURES / "C2_经费预算表.pdf"
C3 = FIXTURES / "C3_科研诚信与合规承诺书.pdf"
C2_NO_UNIT = FIXTURES / "C2_经费预算表_设备无单价.pdf"


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


def _add_material(
    db: Session,
    project_id: str,
    path: Path,
    category: str,
    *,
    status: str = MaterialStatus.READY.value,
    filename: str | None = None,
) -> Material:
    material_id = str(uuid.uuid4())
    dest = material_file_path(material_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    material = Material(
        id=material_id,
        project_id=project_id,
        original_filename=filename or path.name,
        category=category,
        page_count=inspect_pdf(dest) if status == MaterialStatus.READY.value else None,
        status=status,
        storage_path=relative_storage_key(material_id),
    )
    db.add(material)
    db.commit()
    return material


def _seed(db: Session, files: list[tuple[Path, str]], name: str = "B10") -> str:
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name=name))
    db.commit()
    for path, category in files:
        _add_material(db, project_id, path, category)
    return project_id


def _context(project_id: str, rule_code: str) -> RuleExecutionContext:
    return RuleExecutionContext(
        project_id=project_id,
        review_item_id=str(uuid.uuid4()),
        rule_code=rule_code,
    )


def _run(db: Session, project_id: str, rule_code: str):
    return execute_material_rule(db, _context(project_id, rule_code))


def _write_pdf(path: Path, lines: list[str]) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    leftover = page.insert_textbox(
        pymupdf.Rect(36, 36, 560, 800),
        "\n".join(lines),
        fontname="china-s",
        fontsize=11,
    )
    assert leftover >= 0, lines
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_rule_001_pkg_a_complete_pack_passes(db: Session):
    project_id = _seed(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-A",
    )
    result = _run(db, project_id, "RULE-001")
    assert result.status == ReviewCheckStatus.PASS
    assert "承诺书" in result.summary
    categories = {item.category for item in result.evidence if item.material_id}
    assert categories >= {"APPLICATION", "BUDGET", "COMMITMENT"}


def test_rule_001_missing_commitment_is_confirmed_gap(db: Session):
    project_id = _seed(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
        ],
        name="PKG-A-no-A3",
    )
    result = _run(db, project_id, "RULE-001")
    assert result.status == ReviewCheckStatus.FAIL
    assert "缺件" in result.summary or "缺少" in result.summary
    assert "承诺书" in result.summary
    assert result.data["missing"] == ["科研诚信与合规承诺书"]


def test_rule_001_processing_upload_stays_human(db: Session):
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="processing"))
    db.commit()
    _add_material(db, project_id, A1, MaterialCategory.APPLICATION.value)
    _add_material(db, project_id, A2, MaterialCategory.BUDGET.value)
    _add_material(
        db,
        project_id,
        A3,
        MaterialCategory.COMMITMENT.value,
        status=MaterialStatus.PROCESSING.value,
    )
    result = _run(db, project_id, "RULE-001")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "处理中" in result.summary
    assert result.status != ReviewCheckStatus.FAIL


def test_rule_001_empty_project_is_not_confirmed_missing(db: Session):
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="empty"))
    db.commit()
    result = _run(db, project_id, "RULE-001")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.status != ReviewCheckStatus.FAIL


def test_rule_008_pkg_a_not_applicable(db: Session):
    project_id = _seed(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-A",
    )
    extracted = extract_equipment(A2)
    assert extracted.max_unit_price_yuan is not None
    assert extracted.max_unit_price_yuan < 50_000
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.NOT_APPLICABLE
    assert "不适用" in result.summary


def test_rule_008_pkg_c_98000_missing_attachment(db: Session):
    project_id = _seed(
        db,
        [
            (C1, MaterialCategory.APPLICATION.value),
            (C2, MaterialCategory.BUDGET.value),
            (C3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-C",
    )
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.FAIL
    assert "98000" in result.summary
    assert "附件" in result.summary or "必要性" in result.summary
    assert any(item.normalized_value == 98000 for item in result.evidence)
    fee = [item for item in result.evidence if item.field_name == "设备费"]
    assert fee
    assert "必要性说明" not in (fee[0].quote or "")
    assert "98000" in (fee[0].quote or str(fee[0].normalized_value))


def test_rule_008_necessity_attachment_passes(db: Session):
    project_id = _seed(
        db,
        [
            (C1, MaterialCategory.APPLICATION.value),
            (C2, MaterialCategory.BUDGET.value),
            (C3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-C-attachment",
    )
    _add_material(
        db,
        project_id,
        C2,
        MaterialCategory.OTHER.value,
        filename="设备必要性说明.pdf",
    )
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.PASS
    assert "已提供" in result.summary
    assert any(item.field_name == "设备必要性说明" for item in result.evidence)


def test_rule_008_failed_other_does_not_confirm_missing_attachment(db: Session):
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="pkg-c-failed-other"))
    db.commit()
    _add_material(db, project_id, C1, MaterialCategory.APPLICATION.value)
    _add_material(db, project_id, C2, MaterialCategory.BUDGET.value)
    _add_material(db, project_id, C3, MaterialCategory.COMMITMENT.value)
    _add_material(
        db,
        project_id,
        C2,
        MaterialCategory.OTHER.value,
        status=MaterialStatus.FAILED.value,
        filename="补充材料.pdf",
    )
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.status != ReviewCheckStatus.FAIL
    assert "损坏" in result.summary or "无法读取" in result.summary


def test_rule_008_equipment_without_unit_price_is_human(db: Session):
    project_id = _seed(
        db,
        [
            (C1, MaterialCategory.APPLICATION.value),
            (C2_NO_UNIT, MaterialCategory.BUDGET.value),
            (C3, MaterialCategory.COMMITMENT.value),
        ],
        name="CASE-024",
    )
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "单价" in result.summary
    assert result.status != ReviewCheckStatus.FAIL


def test_rule_008_processing_does_not_confirm_missing_attachment(db: Session):
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="pkg-c-processing"))
    db.commit()
    _add_material(db, project_id, C1, MaterialCategory.APPLICATION.value)
    _add_material(db, project_id, C2, MaterialCategory.BUDGET.value)
    _add_material(
        db,
        project_id,
        C3,
        MaterialCategory.COMMITMENT.value,
        status=MaterialStatus.PROCESSING.value,
    )
    result = _run(db, project_id, "RULE-008")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "处理中" in result.summary


def test_rule_009_pkg_a_not_involved_passes(db: Session):
    project_id = _seed(
        db,
        [
            (A1, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-A",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.PASS
    assert "不涉及" in result.summary
    assert any("不涉及人体受试者" in (item.quote or "") for item in result.evidence)


def test_rule_009_pkg_c_unclear_lists_basis_and_questions(db: Session):
    project_id = _seed(
        db,
        [
            (C1, MaterialCategory.APPLICATION.value),
            (C2, MaterialCategory.BUDGET.value),
            (C3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-C",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "依据" in result.summary
    assert "待确认" in result.summary
    assert result.data.get("involved") is True
    questions = result.data.get("pending_questions") or []
    assert questions
    assert any("脑电" in item or "伦理" in item for item in questions)
    assert any(item.quote for item in result.evidence)


def test_rule_009_pkg_b_questionnaire_unclear(db: Session):
    project_id = _seed(
        db,
        [
            (B1, MaterialCategory.APPLICATION.value),
            (B2, MaterialCategory.BUDGET.value),
            (B3, MaterialCategory.COMMITMENT.value),
        ],
        name="PKG-B",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.data.get("pending_questions")


def test_rule_009_recruit_subjects_without_approval_fails(db: Session):
    project_id = _seed(
        db,
        [
            (A1_SUBJECTS, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
            (A3, MaterialCategory.COMMITMENT.value),
        ],
        name="CASE-027",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.FAIL
    assert "受试者" in result.summary or "伦理" in result.summary


def test_rule_009_no_ethics_language_is_human(db: Session):
    project_id = _seed(
        db,
        [
            (A2, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
        ],
        name="silent-ethics",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.status != ReviewCheckStatus.PASS
    assert "不直接通过" in result.summary
    assert result.data.get("pending_questions")


def test_rule_009_involved_with_approval_passes_despite_unclear_authorization(db: Session, tmp_path: Path):
    path = _write_pdf(
        tmp_path / "ethics_approved.pdf",
        [
            "本项目明确招募20名受试者，采集操作负荷。",
            "拟使用合作单位提供的去标识化历史脑电数据。",
            "伦理审批编号：ETH-2026-001。",
            "现阶段材料未附合作单位的数据授权文件。",
        ],
    )
    project_id = _seed(
        db,
        [
            (path, MaterialCategory.APPLICATION.value),
            (A2, MaterialCategory.BUDGET.value),
        ],
        name="ethics-approved",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.PASS
    assert result.data.get("involved") is True
    assert result.data.get("has_approval") is True
    assert "审批编号" in result.summary or "进行中" in result.summary


def test_rule_009_failed_file_does_not_confirm_missing_approval(db: Session):
    project_id = str(uuid.uuid4())
    db.add(Project(id=project_id, name="case-027-failed"))
    db.commit()
    _add_material(db, project_id, A1_SUBJECTS, MaterialCategory.APPLICATION.value)
    _add_material(db, project_id, A2, MaterialCategory.BUDGET.value)
    _add_material(db, project_id, A3, MaterialCategory.COMMITMENT.value)
    _add_material(
        db,
        project_id,
        A3,
        MaterialCategory.OTHER.value,
        status=MaterialStatus.FAILED.value,
        filename="伦理附件.pdf",
    )
    result = _run(db, project_id, "RULE-009")
    assert result.status == ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.status != ReviewCheckStatus.FAIL
    assert "损坏" in result.summary or "无法读取" in result.summary


def test_unknown_rule_is_system_error(db: Session):
    project_id = _seed(db, [], name="unsupported")
    result = _run(db, project_id, "RULE-099")
    assert result.status == ReviewCheckStatus.SYSTEM_ERROR
