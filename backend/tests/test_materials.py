"""Material upload, list, and page-read tests (no cloud calls)."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"


def _create_project(client, name: str = "B02 材料测试项目") -> str:
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


def test_upload_two_pdfs_and_list(client):
    project_id = _create_project(client)

    app_resp = _upload(client, project_id, A1, "APPLICATION")
    assert app_resp.status_code == 201
    application = app_resp.json()
    assert application["original_filename"] == "A1_项目申报书.pdf"
    assert application["category"] == "APPLICATION"
    assert application["page_count"] == 3
    assert application["status"] == "READY"
    assert application["error_summary"] is None

    budget_resp = _upload(client, project_id, A2, "BUDGET")
    assert budget_resp.status_code == 201
    budget = budget_resp.json()
    assert budget["original_filename"] == "A2_经费预算表.pdf"
    assert budget["category"] == "BUDGET"
    assert budget["page_count"] == 2
    assert budget["status"] == "READY"

    listed = client.get(f"/api/projects/{project_id}/materials")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 2
    by_name = {item["original_filename"]: item for item in body}
    assert by_name["A1_项目申报书.pdf"]["page_count"] == 3
    assert by_name["A2_经费预算表.pdf"]["category"] == "BUDGET"


def test_page_text_contains_known_content(client):
    project_id = _create_project(client, "文本页测试")
    material_id = _upload(client, project_id, A1, "APPLICATION").json()["id"]

    page1 = client.get(f"/api/materials/{material_id}/pages/1/text")
    assert page1.status_code == 200
    text = page1.json()["text"]
    assert "项目申报书" in text
    assert "林书言" in text
    assert "30.00 万元" in text
    assert page1.json()["page_number"] == 1
    assert page1.json()["page_count"] == 3

    page2 = client.get(f"/api/materials/{material_id}/pages/2/text")
    assert page2.status_code == 200
    assert "研究方案与前期基础" in page2.json()["text"]
    assert page2.json()["text"] != text


def test_page_image_is_png(client):
    project_id = _create_project(client, "页图测试")
    material_id = _upload(client, project_id, A2, "BUDGET").json()["id"]

    response = client.get(f"/api/materials/{material_id}/pages/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(response.content) > 100


def test_page_bounds_errors(client):
    project_id = _create_project(client, "页码边界")
    material_id = _upload(client, project_id, A2, "BUDGET").json()["id"]

    zero = client.get(f"/api/materials/{material_id}/pages/0/text")
    assert zero.status_code == 400
    assert "页码超出范围" in zero.json()["detail"]

    over = client.get(f"/api/materials/{material_id}/pages/99/image")
    assert over.status_code == 400
    assert "页码超出范围" in over.json()["detail"]


def test_reject_non_pdf(client, tmp_path):
    project_id = _create_project(client, "非 PDF")
    fake = tmp_path / "notes.txt"
    fake.write_text("not a pdf", encoding="utf-8")
    with fake.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": "OTHER"},
            files={"file": ("notes.txt", handle, "text/plain")},
        )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_corrupt_pdf_marks_failed(client, tmp_path):
    project_id = _create_project(client, "损坏 PDF")
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nthis is not a valid pdf body")
    with corrupt.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": "OTHER"},
            files={"file": ("broken.pdf", handle, "application/pdf")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_summary"]
    assert body["page_count"] is None

    listed = client.get(f"/api/projects/{project_id}/materials")
    assert listed.json()[0]["status"] == "FAILED"

    page = client.get(f"/api/materials/{body['id']}/pages/1/text")
    assert page.status_code == 400


def test_unknown_project_and_material(client):
    missing_project = client.post(
        "/api/projects/00000000-0000-0000-0000-000000000000/materials",
        data={"category": "APPLICATION"},
        files={"file": ("a.pdf", b"%PDF-1.4\n%", "application/pdf")},
    )
    assert missing_project.status_code == 404
    assert missing_project.json()["detail"] == "项目不存在"

    missing_list = client.get("/api/projects/00000000-0000-0000-0000-000000000000/materials")
    assert missing_list.status_code == 404

    missing_material = client.get(
        "/api/materials/00000000-0000-0000-0000-000000000000/pages/1/text"
    )
    assert missing_material.status_code == 404
    assert missing_material.json()["detail"] == "材料不存在"


def test_missing_file_on_disk(client):
    project_id = _create_project(client, "文件丢失")
    material = _upload(client, project_id, A1, "APPLICATION").json()
    material_id = material["id"]

    from app.config import get_settings
    from app.services.storage import material_file_path

    path = material_file_path(material_id, get_settings())
    assert path.is_file()
    path.unlink()

    response = client.get(f"/api/materials/{material_id}/pages/1/text")
    assert response.status_code == 404
    assert "丢失" in response.json()["detail"]


def test_invalid_category(client):
    project_id = _create_project(client, "非法类别")
    with A1.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": "INVOICE"},
            files={"file": (A1.name, handle, "application/pdf")},
        )
    assert response.status_code == 422


def test_persistence_across_clients(client):
    """Upload once; a fresh TestClient against the same DB/files still serves pages."""
    project_id = _create_project(client, "持久化")
    material = _upload(client, project_id, A2, "BUDGET").json()
    material_id = material["id"]

    from app.config import get_settings
    from app.db import get_db
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as restarted:
        listed = restarted.get(f"/api/projects/{project_id}/materials")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == material_id
        text = restarted.get(f"/api/materials/{material_id}/pages/2/text")
        assert text.status_code == 200
        assert "年度计划与测算依据" in text.json()["text"]
        image = restarted.get(f"/api/materials/{material_id}/pages/2/image")
        assert image.status_code == 200
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    app.dependency_overrides.clear()


def test_safe_original_filename_strips_path_traversal():
    from app.api.materials import _safe_original_filename

    assert _safe_original_filename("../../../etc/passwd.pdf") == "passwd.pdf"
    assert _safe_original_filename("..\\..\\evil.pdf") == "evil.pdf"
    assert "/" not in _safe_original_filename("a/b/c.pdf")
    assert "\\" not in _safe_original_filename("a\\b\\c.pdf")
    assert ".." not in _safe_original_filename("....pdf")
    sanitized = _safe_original_filename("../..//weird..name.pdf")
    assert "/" not in sanitized
    assert "\\" not in sanitized
    assert ".." not in sanitized
    assert sanitized.endswith(".pdf")


def test_upload_rejects_filename_traversal_in_stored_name(client):
    project_id = _create_project(client, "文件名清洗")
    with A1.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": "APPLICATION"},
            files={"file": ("../../../etc/passwd.pdf", handle, "application/pdf")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "passwd.pdf"
    assert "/" not in body["original_filename"]
    assert ".." not in body["original_filename"]


def test_upload_rejects_oversized_file(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1024")
    get_settings.cache_clear()
    try:
        project_id = _create_project(client, "超大文件")
        # Build a PDF-looking payload larger than 1KB.
        oversized = tmp_path / "big.pdf"
        oversized.write_bytes(b"%PDF-1.4\n" + b"x" * 2048)
        with oversized.open("rb") as handle:
            response = client.post(
                f"/api/projects/{project_id}/materials",
                data={"category": "OTHER"},
                files={"file": ("big.pdf", handle, "application/pdf")},
            )
        assert response.status_code == 413
        assert "过大" in response.json()["detail"]
    finally:
        get_settings.cache_clear()
