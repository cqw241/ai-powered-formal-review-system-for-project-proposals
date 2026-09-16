"""B03 funding extraction, comparison, evidence, and API tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.services.funding_extract import extract_application_funding_from_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"
A2_DIFF = FIXTURES / "A2_经费预算表_320000.pdf"


def _create_project(client, name: str = "B03 经费核对") -> str:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _upload(client, project_id: str, path: Path, category: str):
    with path.open("rb") as handle:
        return client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": category},
            files={"file": (path.name, handle, "application/pdf")},
        )


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


def test_extract_application_vs_budget_unit_normalization():
    app = extract_application_funding_from_pdf(
        A1,
        prefer_labels=("申请经费", "申请金额", "申请资助经费"),
    )
    budget = extract_application_funding_from_pdf(
        A2,
        prefer_labels=("申请总额", "申请经费", "申请金额"),
    )
    assert app.reliable and budget.reliable
    assert app.amount_yuan == 300_000
    assert budget.amount_yuan == 300_000
    assert app.raw_value and "万" in app.raw_value
    assert budget.raw_value and "300000" in budget.raw_value.replace(",", "")
    assert app.page_number == 1
    assert budget.page_number == 1
    assert app.bbox is not None
    assert budget.bbox is not None
    assert app.bbox.page_width > 0 and app.bbox.page_height > 0


def test_extract_budget_320000_difference_source():
    budget = extract_application_funding_from_pdf(
        A2_DIFF,
        prefer_labels=("申请总额", "申请经费", "申请金额"),
    )
    assert budget.reliable
    assert budget.amount_yuan == 320_000
    assert budget.page_number == 1
    assert budget.bbox is not None


def test_total_funding_not_treated_as_application(tmp_path: Path):
    path = tmp_path / "total_only.pdf"
    _write_pdf(path, ["项目名称 测试", "项目总经费 20 万元", "负责人 张三"])
    result = extract_application_funding_from_pdf(path)
    assert result.reliable is False
    assert result.amount_yuan is None
    assert result.field_kind == "total_funding"
    assert "总经费" in (result.reason or "")


def test_missing_unit_need_human(tmp_path: Path):
    path = tmp_path / "no_unit.pdf"
    _write_pdf(path, ["申请经费 300000", "负责人 张三"])
    result = extract_application_funding_from_pdf(
        path,
        prefer_labels=("申请经费", "申请金额"),
    )
    assert result.reliable is False
    assert result.amount_yuan is None
    assert "单位" in (result.reason or "") or "无法" in (result.reason or "")


def test_api_pass_when_amounts_match(client):
    project_id = _create_project(client, "金额一致")
    assert _upload(client, project_id, A1, "APPLICATION").status_code == 201
    assert _upload(client, project_id, A2, "BUDGET").status_code == 201

    response = client.post(f"/api/projects/{project_id}/funding-review")
    assert response.status_code == 201
    body = response.json()
    assert body["rule_id"] == "RULE-007"
    assert body["status"] == "PASS"
    finding = body["finding"]
    assert finding["check_field"] == "申请经费"
    assert finding["difference_yuan"] == 0
    assert finding["left"]["amount_yuan"] == 300_000
    assert finding["right"]["amount_yuan"] == 300_000
    assert finding["left"]["normalized_amount_yuan"] == 300_000
    assert finding["right"]["normalized_amount_yuan"] == 300_000
    assert finding["left"]["page_number"] == 1
    assert finding["right"]["page_number"] == 1
    assert finding["left"]["bbox"] is not None
    assert finding["right"]["bbox"] is not None
    assert finding["left"]["material_id"]
    assert finding["right"]["material_id"]
    assert finding["left"]["material_id"] != finding["right"]["material_id"]

    loaded = client.get(f"/api/projects/{project_id}/funding-review")
    assert loaded.status_code == 200
    assert loaded.json()["id"] == body["id"]
    assert loaded.json()["status"] == "PASS"


def test_api_fail_shows_20000_difference(client):
    project_id = _create_project(client, "金额差异 320000")
    assert _upload(client, project_id, A1, "APPLICATION").status_code == 201
    assert _upload(client, project_id, A2_DIFF, "BUDGET").status_code == 201

    response = client.post(f"/api/projects/{project_id}/funding-review")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAIL"
    finding = body["finding"]
    assert finding["difference_yuan"] == 20_000
    assert finding["left"]["amount_yuan"] == 300_000
    assert finding["right"]["amount_yuan"] == 320_000
    assert "20000" in finding["reason"] or "20,000" in finding["reason"] or "差额 20000" in finding["reason"]
    assert finding["check_field"] == "申请经费"
    assert finding["left"]["raw_value"]
    assert finding["right"]["raw_value"]
    assert finding["left"]["page_number"] == 1
    assert finding["right"]["page_number"] == 1


def test_api_need_human_when_materials_missing(client):
    project_id = _create_project(client, "缺材料")
    response = client.post(f"/api/projects/{project_id}/funding-review")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEED_HUMAN_REVIEW"
    assert body["finding"]["difference_yuan"] is None
    assert "缺少" in body["finding"]["reason"]


def test_api_need_human_when_only_total_funding(client, tmp_path: Path):
    project_id = _create_project(client, "总经费混淆")
    app_path = tmp_path / "app_total.pdf"
    budget_path = tmp_path / "budget_ok.pdf"
    _write_pdf(app_path, ["项目申报书", "项目总经费 20 万元"])
    _write_pdf(budget_path, ["经费预算表", "申请总额 150000 元"])

    assert _upload(client, project_id, app_path, "APPLICATION").status_code == 201
    assert _upload(client, project_id, budget_path, "BUDGET").status_code == 201

    response = client.post(f"/api/projects/{project_id}/funding-review")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEED_HUMAN_REVIEW"
    assert body["finding"]["difference_yuan"] is None
    # Must not invent zero.
    left = body["finding"]["left"]
    assert left is not None
    assert left["amount_yuan"] is None
    assert left["reliable"] is False


def test_funding_review_persists_across_requests(client):
    project_id = _create_project(client, "持久化")
    _upload(client, project_id, A1, "APPLICATION")
    _upload(client, project_id, A2, "BUDGET")
    first = client.post(f"/api/projects/{project_id}/funding-review").json()
    second = client.get(f"/api/projects/{project_id}/funding-review").json()
    assert first["id"] == second["id"]
    assert second["finding"]["status"] == "PASS"


def test_get_funding_review_404_when_absent(client):
    project_id = _create_project(client, "无结果")
    response = client.get(f"/api/projects/{project_id}/funding-review")
    assert response.status_code == 404


def test_unknown_project_funding_review_404(client):
    response = client.post("/api/projects/00000000-0000-0000-0000-000000000000/funding-review")
    assert response.status_code == 404
