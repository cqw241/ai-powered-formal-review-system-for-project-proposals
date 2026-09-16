"""Policy upload, preview, candidate extraction, and edit persistence tests."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"
A1 = FIXTURES / "A1_项目申报书.pdf"


def _upload_policy(client, path: Path = POLICY_PDF):
    with path.open("rb") as handle:
        return client.post(
            "/api/policies",
            files={"file": (path.name, handle, "application/pdf")},
        )


def test_upload_policy_extracts_funding_caps(client):
    response = _upload_policy(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "READY"
    assert body["page_count"] == 2
    assert body["original_filename"] == POLICY_PDF.name
    assert body["title"]
    candidates = body["candidates"]
    funding = [c for c in candidates if c["kind"] == "FUNDING_CAP"]
    assert len(funding) == 2
    by_cat = {c["category"]: c for c in funding}
    assert by_cat["自然科学类"]["amount_yuan"] == 300_000
    assert by_cat["自然科学类"]["amount_raw"]
    assert "30" in by_cat["自然科学类"]["amount_raw"]
    assert by_cat["自然科学类"]["source_clause"] == "P-05"
    assert by_cat["自然科学类"]["source_page"] == 1
    assert by_cat["自然科学类"]["source_quote"]
    assert by_cat["人文社会科学类"]["amount_yuan"] == 150_000
    assert by_cat["人文社会科学类"]["source_clause"] == "P-05"


def test_list_and_get_policy(client):
    uploaded = _upload_policy(client).json()
    listed = client.get("/api/policies")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) >= 1
    assert rows[0]["id"] == uploaded["id"]
    assert rows[0]["candidate_count"] == len(uploaded["candidates"])

    detail = client.get(f"/api/policies/{uploaded['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == uploaded["id"]
    assert len(detail.json()["candidates"]) == len(uploaded["candidates"])


def test_policy_page_text_and_image(client):
    policy_id = _upload_policy(client).json()["id"]

    page1 = client.get(f"/api/policies/{policy_id}/pages/1/text")
    assert page1.status_code == 200
    text = page1.json()["text"]
    assert "P-05" in text
    assert "申请经费上限" in text
    assert page1.json()["page_count"] == 2

    page2 = client.get(f"/api/policies/{policy_id}/pages/2/text")
    assert page2.status_code == 200
    assert "P-06" in page2.json()["text"]

    image = client.get(f"/api/policies/{policy_id}/pages/1/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"

    bad = client.get(f"/api/policies/{policy_id}/pages/0/text")
    assert bad.status_code == 400


def test_edit_candidate_persists_after_reload(client):
    uploaded = _upload_policy(client).json()
    funding = [c for c in uploaded["candidates"] if c["kind"] == "FUNDING_CAP"]
    target = next(c for c in funding if c["category"] == "人文社会科学类")

    patched = client.patch(
        f"/api/policies/{uploaded['id']}/candidates/{target['id']}",
        json={
            "category": "人文社会科学类（已修订）",
            "amount_raw": "14万元",
            "source_quote": "人工修订摘录：人文社科不超过14万元",
            "title": "申请经费上限（修订）",
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["category"] == "人文社会科学类（已修订）"
    assert body["amount_raw"] == "14万元"
    assert body["amount_yuan"] == 140_000
    assert body["amount_unit"] == "万元"
    assert body["title"] == "申请经费上限（修订）"
    assert "人工修订摘录" in body["source_quote"]

    reloaded = client.get(f"/api/policies/{uploaded['id']}").json()
    found = next(c for c in reloaded["candidates"] if c["id"] == target["id"])
    assert found["category"] == "人文社会科学类（已修订）"
    assert found["amount_yuan"] == 140_000
    assert found["source_quote"].startswith("人工修订摘录")


def test_edit_unknown_amount_not_zero(client):
    uploaded = _upload_policy(client).json()
    target = next(c for c in uploaded["candidates"] if c["kind"] == "FUNDING_CAP")
    patched = client.patch(
        f"/api/policies/{uploaded['id']}/candidates/{target['id']}",
        json={"amount_raw": "待确认"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["amount_raw"] == "待确认"
    assert body["amount_yuan"] is None


def test_reject_non_pdf_and_unknown_policy(client):
    bad = client.post(
        "/api/policies",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert bad.status_code == 400

    missing = client.get("/api/policies/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_upload_size_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("UPLOAD_MAX_BYTES", "100")
    get_settings.cache_clear()
    try:
        response = _upload_policy(client, A1)
        assert response.status_code == 413
    finally:
        monkeypatch.setenv("UPLOAD_MAX_BYTES", str(20 * 1024 * 1024))
        get_settings.cache_clear()
