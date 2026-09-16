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


def _item(body: dict, rule_code: str) -> dict:
    matches = [item for item in body["items"] if item["rule_code"] == rule_code]
    assert matches, rule_code
    return matches[0]


BUILTIN_CODES = ["RULE-002", "RULE-003", "RULE-004", "RULE-006", "RULE-007", "RULE-010"]


def test_review_without_enabled_rules_runs_builtins(client):
    project_id = _create_project_with_materials(client)
    created = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert [item["rule_code"] for item in body["items"]] == BUILTIN_CODES
    item = _item(body, "RULE-007")
    assert item["status"] == "COMPLETED"
    assert item["check_status"] == "PASS"
    assert item["source_rule_id"] is None
    assert "300000" in item["summary"] or "300,000" in item["summary"] or "一致" in item["summary"]
    assert item["funding_review_id"]
    assert item["result"]["evidence"]

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
    assert codes == ["RULE-002", "RULE-003", "RULE-004", "RULE-005", "RULE-006", "RULE-007", "RULE-010"]
    rule007 = _item(body, "RULE-007")
    selected = _item(body, "RULE-005")
    assert rule007["status"] == "COMPLETED"
    assert rule007["check_status"] == "PASS"
    assert selected["status"] == "COMPLETED"
    assert selected["check_status"] == "FAIL"
    assert selected["source_rule_id"] == natural["id"]
    assert selected["version_number"] == 2
    assert selected["snapshot"]["amount_yuan"] == 280_000
    assert selected["snapshot"]["category"] == "自然科学类"
    assert selected["snapshot"]["application_amount_yuan"] == 300_000
    assert "超出上限" in selected["summary"]
    assert humanities["id"] not in {item["source_rule_id"] for item in body["items"]}

    reloaded = client.get(f"/api/projects/{project_id}/reviews/{body['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["id"] == body["id"]
    assert _item(reloaded.json(), "RULE-005")["status"] == "COMPLETED"

    history = client.get(f"/api/projects/{project_id}/reviews")
    assert history.status_code == 200
    assert history.json()[0]["id"] == body["id"]


def test_rule_fail_is_completed_item_not_task_failure(client):
    project_id = _create_project_with_materials(client, budget=A2_DIFF)
    body = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []}).json()
    assert body["status"] == "COMPLETED"
    item = _item(body, "RULE-007")
    assert item["check_status"] == "FAIL"
    assert item["status"] == "COMPLETED"
    assert item["status"] != "FAILED"


def test_missing_materials_are_pending_confirmation(client):
    project_id = _create_project(client, "缺材料")
    body = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []}).json()
    item = _item(body, "RULE-007")
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
    item = _item(body, "RULE-007")
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
    assert "RULE-007" in [item["rule_code"] for item in running["items"]]
    assert "RULE-005" in [item["rule_code"] for item in running["items"]]
    rule007 = _item(running, "RULE-007")
    assert rule007["status"] == "RUNNING"
    assert rule007["check_status"] is None
    assert "正在执行" in rule007["summary"]
    final = created.json()
    assert final["id"] == running["id"]
    assert final["status"] == "COMPLETED"
    assert _item(final, "RULE-007")["status"] == "COMPLETED"
    assert _item(final, "RULE-005")["status"] == "COMPLETED"
    assert _item(final, "RULE-005")["snapshot"]["application_amount_yuan"] == 300_000


def test_unexpected_funding_error_does_not_leave_running_task(client, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("funding service exploded")

    monkeypatch.setattr("app.services.reviews.funding_service.run_funding_review", _boom)
    project_id = _create_project_with_materials(client)
    created = client.post(f"/api/projects/{project_id}/reviews", json={"rule_ids": []})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    item = _item(body, "RULE-007")
    assert item["status"] == "FAILED"
    assert item["check_status"] == "SYSTEM_ERROR"
    assert "funding service exploded" in item["summary"]
    assert all(row["status"] != "RUNNING" for row in body["items"])
    listed = client.get(f"/api/projects/{project_id}/reviews").json()
    assert listed[0]["status"] == "COMPLETED"
    assert _item(listed[0], "RULE-007")["status"] == "FAILED"


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
    rule007 = _item(workspace.json(), "RULE-007")
    assert rule007["funding_review_id"] != funding.json()["id"]
    latest = client.get(f"/api/projects/{project_id}/funding-review").json()
    assert latest["id"] == rule007["funding_review_id"]
