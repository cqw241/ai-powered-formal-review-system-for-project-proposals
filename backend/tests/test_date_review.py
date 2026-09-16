"""B09 RULE-004/010: period window, duration, signing date, and incomplete-date HUMAN."""

from __future__ import annotations

import shutil
import uuid
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base
from app.models import Material, MaterialCategory, MaterialStatus, Project
from app.review_contract import ReviewCheckStatus, RuleExecutionContext, RuleExecutionResult
from app.services.date_extract import (
    ExtractedDate,
    ExtractedPeriod,
    extract_execution_period_from_pdf,
    extract_signing_date_from_pdf,
    parse_date_text,
    parse_period_text,
)
from app.services.date_review import (
    PERIOD_MAX_MONTHS,
    PERIOD_WINDOW_END,
    PERIOD_WINDOW_START,
    SIGNING_DEADLINE,
    evaluate_period_rule,
    evaluate_signing_rule,
    execute_date_rule,
    period_within_max_months,
)
from app.services.storage import material_file_path, relative_storage_key

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
A1 = FIXTURES / "A1_项目申报书.pdf"
A3 = FIXTURES / "A3_科研诚信与合规承诺书.pdf"
B1 = FIXTURES / "B1_项目申报书.pdf"
B3 = FIXTURES / "B3_科研诚信与合规承诺书.pdf"
C1 = FIXTURES / "C1_项目申报书.pdf"
C3 = FIXTURES / "C3_科研诚信与合规承诺书.pdf"
A1_VAGUE = FIXTURES / "A1_项目申报书_周期约两年.pdf"
B3_OCCLUDED = FIXTURES / "B3_承诺书_签署日期遮挡.pdf"


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


def _extracted_date(
    field: str, parsed: date | None, *, reliable: bool = True, raw: str | None = None
) -> ExtractedDate:
    iso = parsed.isoformat() if parsed is not None else None
    return ExtractedDate(
        field_name=field,
        raw_value=raw if raw is not None else iso,
        iso_value=iso if reliable else None,
        parsed=parsed if reliable else None,
        page_number=1,
        quote=raw or iso,
        bbox=None,
        reliable=reliable,
        reason=None if reliable else "日期无法可靠确定",
    )


def _period(
    start: date | None, end: date | None, *, reliable: bool = True, raw: str | None = None
) -> ExtractedPeriod:
    start_ext = _extracted_date("开始日期", start, reliable=reliable and start is not None, raw=raw)
    end_ext = _extracted_date("结束日期", end, reliable=reliable and end is not None, raw=raw)
    return ExtractedPeriod(
        start=start_ext,
        end=end_ext,
        raw_value=raw,
        page_number=1,
        quote=raw,
    )


def _context(project_id: str, rule_code: str) -> RuleExecutionContext:
    return RuleExecutionContext(
        project_id=project_id,
        review_item_id=str(uuid.uuid4()),
        rule_code=rule_code,
    )


@pytest.fixture()
def date_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Session:
    db_path = tmp_path / "date.db"
    materials_path = tmp_path / "materials"
    materials_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MATERIALS_DIR", str(materials_path))
    get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
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
        engine.dispose()
        get_settings.cache_clear()


def _seed_material(db: Session, pdf_path: Path, category: MaterialCategory) -> tuple[str, Material]:
    project_id = str(uuid.uuid4())
    material_id = str(uuid.uuid4())
    dest = material_file_path(material_id)
    shutil.copyfile(pdf_path, dest)
    db.add(Project(id=project_id, name="B09 date rules"))
    material = Material(
        id=material_id,
        project_id=project_id,
        original_filename=pdf_path.name,
        category=category.value,
        page_count=1,
        status=MaterialStatus.READY.value,
        storage_path=relative_storage_key(material_id),
    )
    db.add(material)
    db.commit()
    return project_id, material


# --- calendar predicates ---


def test_boundary_window_is_exactly_24_months():
    start = date(2027, 1, 1)
    end = date(2028, 12, 31)
    assert start == PERIOD_WINDOW_START
    assert end == PERIOD_WINDOW_END
    assert period_within_max_months(start, end, PERIOD_MAX_MONTHS) is True
    assert period_within_max_months(start, date(2029, 1, 1), PERIOD_MAX_MONTHS) is False


# --- RULE-004 evaluation ---


def test_period_boundary_dates_pass():
    result = evaluate_period_rule(_period(date(2027, 1, 1), date(2028, 12, 31)))
    assert isinstance(result, RuleExecutionResult)
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["start_ok"] is True
    assert result.data["end_ok"] is True
    assert result.data["duration_ok"] is True
    assert result.data["violations"] == []
    assert "2027-01-01" in result.summary
    assert "2028-12-31" in result.summary


def test_period_end_after_window_fail():
    result = evaluate_period_rule(_period(date(2027, 3, 1), date(2029, 2, 28)))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["start_ok"] is True
    assert result.data["end_ok"] is False
    assert result.data["duration_ok"] is True
    assert "end_after_window" in result.data["violations"]
    assert "2029-02-28" in result.summary
    assert "2028-12-31" in result.summary


def test_period_start_before_window_fail():
    result = evaluate_period_rule(_period(date(2026, 12, 31), date(2028, 12, 31)))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["start_ok"] is False
    assert "start_before_window" in result.data["violations"]
    assert "2026-12-31" in result.summary


def test_period_duration_over_24_months_fail():
    result = evaluate_period_rule(_period(date(2027, 3, 1), date(2029, 3, 1)))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["duration_ok"] is False
    assert "duration_over_max" in result.data["violations"]
    assert "24" in result.summary


def test_period_incomplete_need_human():
    result = evaluate_period_rule(
        parse_period_text("计划执行约两年，立项后启动"),
    )
    assert result.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.data["start_date"] is None
    assert result.data["end_date"] is None
    assert "约两年" in result.summary
    assert result.evidence
    assert all(item.reliable is False for item in result.evidence)


# --- RULE-010 evaluation ---


def test_signing_on_deadline_pass():
    result = evaluate_signing_rule(_extracted_date("签署日期", SIGNING_DEADLINE))
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["deadline_ok"] is True
    assert SIGNING_DEADLINE.isoformat() in result.summary


def test_signing_after_deadline_fail():
    result = evaluate_signing_rule(_extracted_date("签署日期", date(2026, 10, 2)))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["deadline_ok"] is False
    assert "2026-10-02" in result.summary
    assert "2026-09-30" in result.summary


def test_signing_incomplete_need_human():
    extracted = parse_date_text("2026-09-2?", field_name="签署日期")
    result = evaluate_signing_rule(extracted)
    assert extracted.reliable is False
    assert result.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "2026-09-2" in (result.summary + (extracted.raw_value or ""))
    assert result.evidence[0].reliable is False
    assert result.evidence[0].raw_value


# --- native-text extraction from fixtures ---


def test_extract_pkg_a_period_boundary():
    period = extract_execution_period_from_pdf(A1)
    assert period.start.reliable and period.end.reliable
    assert period.start.parsed == date(2027, 1, 1)
    assert period.end.parsed == date(2028, 12, 31)
    assert period.start.page_number == 1
    assert period.start.bbox is not None
    assert period.end.bbox is not None


def test_extract_pkg_b_period_over_window():
    period = extract_execution_period_from_pdf(B1)
    assert period.start.parsed == date(2027, 3, 1)
    assert period.end.parsed == date(2029, 2, 28)


def test_extract_vague_period_unreliable():
    period = extract_execution_period_from_pdf(A1_VAGUE)
    assert period.start.reliable is False
    assert period.end.reliable is False
    assert period.start.parsed is None
    assert "约两年" in (period.raw_value or "")
    assert period.quote


def test_extract_pkg_a_signing_chinese_date():
    extracted = extract_signing_date_from_pdf(A3)
    assert extracted.reliable is True
    assert extracted.parsed == date(2026, 9, 18)
    assert extracted.page_number == 2
    assert extracted.bbox is not None


def test_extract_occluded_signing_unreliable():
    extracted = extract_signing_date_from_pdf(B3_OCCLUDED)
    assert extracted.reliable is False
    assert extracted.parsed is None
    assert extracted.raw_value
    assert "2026-09-2" in extracted.raw_value


def test_parse_chinese_and_iso_dates():
    chinese = parse_date_text("2026 年 9 月 18 日")
    iso = parse_date_text("2026-09-18")
    assert chinese.parsed == iso.parsed == date(2026, 9, 18)
    incomplete = parse_date_text("2026-09-2学院章")
    assert incomplete.reliable is False
    assert incomplete.parsed is None


# --- execute_date_rule against seeded materials ---


def test_execute_rule004_pkg_a_pass(date_db: Session):
    project_id, material = _seed_material(date_db, A1, MaterialCategory.APPLICATION)
    result = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    assert isinstance(result, RuleExecutionResult)
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["start_date"] == "2027-01-01"
    assert result.data["end_date"] == "2028-12-31"
    assert result.evidence[0].material_id == material.id
    assert result.evidence[0].category == MaterialCategory.APPLICATION.value
    assert result.evidence[0].page_number == 1
    assert result.evidence[0].bbox is not None


def test_execute_rule004_pkg_b_end_overdue(date_db: Session):
    project_id, _material = _seed_material(date_db, B1, MaterialCategory.APPLICATION)
    result = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    assert result.status is ReviewCheckStatus.FAIL
    assert "2029-02-28" in result.summary
    assert "2028-12-31" in result.summary
    assert result.data["end_ok"] is False


def test_execute_rule004_pkg_c_within_window(date_db: Session):
    project_id, _material = _seed_material(date_db, C1, MaterialCategory.APPLICATION)
    result = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["start_date"] == "2027-01-15"
    assert result.data["end_date"] == "2028-10-31"


def test_execute_rule004_incomplete_period(date_db: Session):
    project_id, _material = _seed_material(date_db, A1_VAGUE, MaterialCategory.APPLICATION)
    result = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    assert result.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert "约两年" in result.summary
    assert result.evidence
    assert result.evidence[0].quote


def test_execute_rule004_start_before_window(date_db: Session, tmp_path: Path):
    path = tmp_path / "early.pdf"
    _write_pdf(path, ["项目申报书", "执行期 2026-12-31 至 2028-12-31"])
    project_id, _material = _seed_material(date_db, path, MaterialCategory.APPLICATION)
    result = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["start_ok"] is False
    assert "2026-12-31" in result.summary


def test_execute_rule010_pkg_a_pass(date_db: Session):
    project_id, material = _seed_material(date_db, A3, MaterialCategory.COMMITMENT)
    result = execute_date_rule(date_db, _context(project_id, "RULE-010"))
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["signing_date"] == "2026-09-18"
    assert result.evidence[0].material_id == material.id
    assert result.evidence[0].normalized_value == "2026-09-18"
    assert result.evidence[0].bbox is not None


def test_execute_rule010_pkg_b_signing_overdue(date_db: Session):
    project_id, _material = _seed_material(date_db, B3, MaterialCategory.COMMITMENT)
    result = execute_date_rule(date_db, _context(project_id, "RULE-010"))
    assert result.status is ReviewCheckStatus.FAIL
    assert result.data["signing_date"] == "2026-10-02"
    assert "2026-10-02" in result.summary
    assert "2026-09-30" in result.summary


def test_execute_rule010_pkg_c_pass(date_db: Session):
    project_id, _material = _seed_material(date_db, C3, MaterialCategory.COMMITMENT)
    result = execute_date_rule(date_db, _context(project_id, "RULE-010"))
    assert result.status is ReviewCheckStatus.PASS
    assert result.data["signing_date"] == "2026-09-26"


def test_execute_rule010_occluded_signing(date_db: Session):
    project_id, _material = _seed_material(date_db, B3_OCCLUDED, MaterialCategory.COMMITMENT)
    result = execute_date_rule(date_db, _context(project_id, "RULE-010"))
    assert result.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert result.evidence[0].raw_value
    assert "2026-09-2" in (result.evidence[0].raw_value or "")
    assert result.evidence[0].quote


def test_execute_missing_material_need_human(date_db: Session):
    project_id = str(uuid.uuid4())
    date_db.add(Project(id=project_id, name="no materials"))
    date_db.commit()
    period = execute_date_rule(date_db, _context(project_id, "RULE-004"))
    signing = execute_date_rule(date_db, _context(project_id, "RULE-010"))
    assert period.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert signing.status is ReviewCheckStatus.NEED_HUMAN_REVIEW
    assert period.evidence == []
    assert signing.evidence == []


def test_execute_unsupported_rule_system_error(date_db: Session):
    project_id = str(uuid.uuid4())
    date_db.add(Project(id=project_id, name="unsupported"))
    date_db.commit()
    result = execute_date_rule(date_db, _context(project_id, "RULE-007"))
    assert result.status is ReviewCheckStatus.SYSTEM_ERROR
    assert "RULE-007" in result.summary
