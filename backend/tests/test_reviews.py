"""B06 review-task contract: RULE-007 plus selected enabled-rule snapshots."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"
A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"
A2_DIFF = FIXTURES / "A2_经费预算表_320000.pdf"


def _upload_policy(client):
    with POLICY_PDF.open("rb") as handle:
        return client.post(
            "/api/policies",
            files={"file": (POLICY_PDF.name, handle, "application/pdf")},
        )


def _funding_cap(uploaded: dict, category: str) -> dict:
    matches = [
        item
        for item in uploaded["candidates"]
        if item["kind"] == "FUNDING_CAP" and item["category"] == category
    ]
    assert matches
    return matches[0]


def _enable(client, policy_id: str, candidate_id: str):
    return client.post(f"/api/policies/{policy_id}/candidates/{candidate_id}/enable")


def _create_project(client, name: str = "B06 审查工作台") -> str:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def _upload(client, project_id: str, path: Path, category: str):
    with path.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": category},
            files={"file": (path.name, handle, "application/pdf")},
        )
    assert response.status_code == 201
    return response


def _create_project_with_materials(client, name: str = "B06 审查工作台", budget: Path = A2) -> str:
    project_id = _create_project(client, name)
    _upload(client, project_id, A1, "APPLICATION")
    _upload(client, project_id, budget, "BUDGET")
    return project_id


def test_review_without_enabled_rules_runs_only_rule_007(client):
    project_id = _create_project_with_materials(client)
    created = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert [item["rule_code"] for item in body["items"]] == ["RULE-007"]
    item = body["items"][0]
    assert item["status"] == "COMPLETED"
    assert item["check_status"] == "PASS"
    assert item["source_rule_id"] is None
    assert "300000" in item["summary"] or "300,000" in item["summary"] or "一致" in item["summary"]
    assert item["funding_review_id"]

    listed = client.get("/api/rules").json()
    assert all(item["rule_code"] != "RULE-007" for item in listed)

    funding = client.get(f"/api/projects/{project_id}/funding-review")
    assert funding.status_code == 200
    assert funding.json()["id"] == item["funding_review_id"]
    assert funding.json()["status"] == "PASS"


def test_selected_rule_is_not_executed_and_binds_version_snapshot(client):
    uploaded = _upload_policy(client).json()
    natural = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    humanities = _enable(
        client, uploaded["id"], _funding_cap(uploaded, "人文社会科学类")["id"]
    ).json()
    edited = client.patch(f"/api/rules/{natural['id']}", json={"amount_raw": "28万元"}).json()
    assert edited["current_version_number"] == 2

    project_id = _create_project_with_materials(client)
    created = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": [natural["id"]]},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    codes = [item["rule_code"] for item in body["items"]]
    assert codes == ["RULE-007", "RULE-005"]
    rule007 = body["items"][0]
    selected = body["items"][1]
    assert rule007["status"] == "COMPLETED"
    assert rule007["check_status"] == "PASS"
    assert selected["status"] == "NOT_EXECUTED"
    assert selected["check_status"] is None
    assert selected["source_rule_id"] == natural["id"]
    assert selected["version_number"] == 2
    assert selected["snapshot"]["amount_yuan"] == 280_000
    assert selected["snapshot"]["category"] == "自然科学类"
    assert selected["snapshot"]["application_amount_yuan"] == 300_000
    assert selected["status"] != "COMPLETED"
    assert selected["status"] != "FAILED"
    assert "不按学科类别上限" in selected["summary"]
    assert humanities["id"] not in {item["source_rule_id"] for item in body["items"]}

    reloaded = client.get(f"/api/projects/{project_id}/reviews/{body['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["id"] == body["id"]
    assert [item["status"] for item in reloaded.json()["items"]] == ["COMPLETED", "NOT_EXECUTED"]

    history = client.get(f"/api/projects/{project_id}/reviews")
    assert history.status_code == 200
    assert history.json()[0]["id"] == body["id"]


def test_rule_fail_is_completed_item_not_task_failure(client):
    project_id = _create_project_with_materials(client, budget=A2_DIFF)
    body = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []}).json()
    assert body["status"] == "COMPLETED"
    item = body["items"][0]
    assert item["check_status"] == "FAIL"
    assert item["status"] == "COMPLETED"
    assert item["status"] != "FAILED"


def test_missing_materials_are_pending_confirmation(client):
    project_id = _create_project(client, "缺材料")
    body = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []}).json()
    item = body["items"][0]
    assert item["check_status"] == "NEED_HUMAN_REVIEW"
    assert item["status"] == "PENDING_CONFIRMATION"
    assert body["status"] == "COMPLETED"


def test_system_error_maps_to_failed_item(client, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(
        "app.services.funding_review.extract_application_funding_from_pdf",
        _boom,
    )
    project_id = _create_project_with_materials(client)
    body = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []}).json()
    item = body["items"][0]
    assert item["check_status"] == "SYSTEM_ERROR"
    assert item["status"] == "FAILED"
    assert body["status"] == "COMPLETED"


def test_disabled_rule_rejected_unknown_rule_404(client):
    uploaded = _upload_policy(client).json()
    enabled = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    client.post(f"/api/rules/{enabled['id']}/disable")
    project_id = _create_project(client)

    disabled = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": [enabled["id"]]},
    )
    assert disabled.status_code == 400
    assert "未启用" in disabled.json()["detail"]

    missing = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "规则不存在"


def test_running_task_persists_items_before_funding_finishes(client, monkeypatch):
    uploaded = _upload_policy(client).json()
    enabled = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    from app.services.funding_review import run_funding_review as original_run

    captured: dict = {}

    def wrapper(db, project_id, **kwargs):
        listed = client.get(f"/api/projects/{project_id}/reviews")
        captured["http_status"] = listed.status_code
        captured["http_body"] = listed.json()
        return original_run(db, project_id, **kwargs)

    monkeypatch.setattr("app.services.reviews.funding_service.run_funding_review", wrapper)
    project_id = _create_project_with_materials(client)
    created = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": [enabled["id"]]},
    )
    assert created.status_code == 201
    assert captured["http_status"] == 200
    running = captured["http_body"][0]
    assert running["status"] == "RUNNING"
    assert [item["rule_code"] for item in running["items"]] == ["RULE-007", "RULE-005"]
    assert running["items"][0]["status"] == "RUNNING"
    assert running["items"][0]["check_status"] is None
    assert "正在执行" in running["items"][0]["summary"]
    assert running["items"][1]["status"] == "NOT_EXECUTED"
    final = created.json()
    assert final["id"] == running["id"]
    assert final["status"] == "COMPLETED"
    assert final["items"][0]["status"] == "COMPLETED"
    assert final["items"][1]["status"] == "NOT_EXECUTED"
    assert final["items"][1]["snapshot"]["application_amount_yuan"] == 300_000


def test_unexpected_funding_error_does_not_leave_running_task(client, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("funding service exploded")

    monkeypatch.setattr("app.services.reviews.funding_service.run_funding_review", _boom)
    project_id = _create_project_with_materials(client)
    created = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    item = body["items"][0]
    assert item["status"] == "FAILED"
    assert item["check_status"] == "SYSTEM_ERROR"
    assert "funding service exploded" in item["summary"]
    listed = client.get(f"/api/projects/{project_id}/reviews").json()
    assert listed[0]["status"] == "COMPLETED"
    assert listed[0]["items"][0]["status"] == "FAILED"


def test_create_review_requires_json_body(client):
    project_id = _create_project(client)
    missing = client.post(f"/api/projects/{project_id}/reviews")
    assert missing.status_code == 422


def test_funding_review_endpoint_still_works_alongside_workspace(client):
    project_id = _create_project_with_materials(client, "B03 回归")
    funding = client.post(f"/api/projects/{project_id}/funding-review")
    assert funding.status_code == 201
    assert funding.json()["rule_id"] == "RULE-007"
    assert funding.json()["status"] == "PASS"

    workspace = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []})
    assert workspace.status_code == 201
    assert workspace.json()["items"][0]["funding_review_id"] != funding.json()["id"]
    latest = client.get(f"/api/projects/{project_id}/funding-review").json()
    assert latest["id"] == workspace.json()["items"][0]["funding_review_id"]
