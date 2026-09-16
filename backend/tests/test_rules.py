"""B05: enable/version/bind/disable RULE-005 without changing RULE-007."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"
A1 = FIXTURES / "A1_项目申报书.pdf"
A2 = FIXTURES / "A2_经费预算表.pdf"


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


def _create_project_with_materials(client, name: str = "B05 经费核对") -> str:
    project_id = client.post("/api/projects", json={"name": name}).json()["id"]
    for path, category in ((A1, "APPLICATION"), (A2, "BUDGET")):
        with path.open("rb") as handle:
            response = client.post(
                f"/api/projects/{project_id}/materials",
                data={"category": category},
                files={"file": (path.name, handle, "application/pdf")},
            )
        assert response.status_code == 201
    return project_id


def test_enable_funding_cap_creates_rule_version_and_persists(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "自然科学类")

    enabled = _enable(client, uploaded["id"], candidate["id"])
    assert enabled.status_code == 200
    body = enabled.json()
    assert body["rule_code"] == "RULE-005"
    assert body["enabled"] is True
    assert body["current_version_number"] == 1
    assert body["current_version"]["category"] == "自然科学类"
    assert body["current_version"]["amount_yuan"] == 300_000
    assert body["current_version"]["compare_field"] == "申请经费"
    assert body["current_version"]["comparator"] == "LE"
    assert body["current_version"]["amount_yuan"] != 0 or candidate["amount_yuan"] == 0

    listed = client.get("/api/rules")
    assert listed.status_code == 200
    assert any(item["id"] == body["id"] for item in listed.json())

    detail = client.get(f"/api/policies/{uploaded['id']}")
    assert detail.status_code == 200
    attached = next(item for item in detail.json()["candidates"] if item["id"] == candidate["id"])
    assert attached["rule"]["id"] == body["id"]
    assert attached["rule"]["enabled"] is True
    assert attached["rule"]["current_version_number"] == 1


def test_enable_clause_candidate_rejected(client):
    uploaded = _upload_policy(client).json()
    clause = next(item for item in uploaded["candidates"] if item["kind"] != "FUNDING_CAP")
    response = _enable(client, uploaded["id"], clause["id"])
    assert response.status_code == 400
    assert "经费上限" in response.json()["detail"]


def test_edit_enabled_rule_appends_version_without_mutating_history(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "人文社会科学类")
    enabled = _enable(client, uploaded["id"], candidate["id"]).json()
    v1_id = enabled["current_version"]["id"]
    v1_amount = enabled["current_version"]["amount_yuan"]
    assert v1_amount == 150_000

    patched = client.patch(
        f"/api/rules/{enabled['id']}",
        json={"amount_raw": "14万元", "category": "人文社会科学类（修订）"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["current_version_number"] == 2
    assert body["current_version"]["amount_yuan"] == 140_000
    assert body["current_version"]["category"] == "人文社会科学类（修订）"
    versions = {item["version_number"]: item for item in body["versions"]}
    assert versions[1]["id"] == v1_id
    assert versions[1]["amount_yuan"] == 150_000
    assert versions[1]["category"] == "人文社会科学类"
    assert versions[2]["amount_yuan"] == 140_000

    reloaded = client.get(f"/api/rules/{enabled['id']}").json()
    assert reloaded["current_version_number"] == 2
    assert reloaded["versions"][0]["amount_yuan"] == 150_000


def test_unknown_amount_not_coerced_to_zero(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "自然科学类")
    enabled = _enable(client, uploaded["id"], candidate["id"]).json()

    patched = client.patch(f"/api/rules/{enabled['id']}", json={"amount_raw": "无法解析的上限"})
    assert patched.status_code == 200
    version = patched.json()["current_version"]
    assert version["amount_raw"] == "无法解析的上限"
    assert version["amount_yuan"] is None


def test_disable_keeps_versions_and_can_reenable(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "自然科学类")
    enabled = _enable(client, uploaded["id"], candidate["id"]).json()
    client.patch(f"/api/rules/{enabled['id']}", json={"amount_raw": "28万元"})

    disabled = client.post(f"/api/rules/{enabled['id']}/disable")
    assert disabled.status_code == 200
    body = disabled.json()
    assert body["enabled"] is False
    assert body["current_version_number"] == 2
    assert len(body["versions"]) == 2

    again = client.post(f"/api/rules/{enabled['id']}/enable")
    assert again.status_code == 200
    assert again.json()["enabled"] is True
    assert again.json()["current_version_number"] == 2


def test_funding_review_binds_enabled_version_and_keeps_old_snapshot(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "自然科学类")
    enabled = _enable(client, uploaded["id"], candidate["id"]).json()
    edited = client.patch(f"/api/rules/{enabled['id']}", json={"amount_raw": "28万元"}).json()
    assert edited["current_version"]["amount_yuan"] == 280_000

    project_id = _create_project_with_materials(client)
    first = client.post(f"/api/projects/{project_id}/funding-review")
    assert first.status_code == 201
    first_body = first.json()
    assert first_body["rule_id"] == "RULE-007"
    assert first_body["status"] == "PASS"
    assert first_body["finding"]["check_field"] == "申请经费"
    assert first_body["finding"]["difference_yuan"] == 0
    bound = first_body["bound_rules"]
    assert len(bound) == 1
    assert bound[0]["rule_code"] == "RULE-005"
    assert bound[0]["version_number"] == 2
    assert bound[0]["amount_yuan"] == 280_000
    assert bound[0]["category"] == "自然科学类"
    assert bound[0]["compare_field"] == "申请经费"
    assert bound[0]["application_amount_yuan"] == 300_000
    assert bound[0]["amount_yuan"] is not None
    assert first_body["status"] != "FAIL" or first_body["finding"]["rule_id"] == "RULE-007"

    client.post(f"/api/rules/{enabled['id']}/disable")
    second = client.post(f"/api/projects/{project_id}/funding-review")
    assert second.status_code == 201
    second_body = second.json()
    assert second_body["id"] != first_body["id"]
    assert second_body["status"] == "PASS"
    assert second_body["bound_rules"] == []

    old = client.get(f"/api/projects/{project_id}/funding-reviews/{first_body['id']}")
    assert old.status_code == 200
    old_body = old.json()
    assert old_body["id"] == first_body["id"]
    assert len(old_body["bound_rules"]) == 1
    assert old_body["bound_rules"][0]["version_number"] == 2
    assert old_body["bound_rules"][0]["amount_yuan"] == 280_000

    latest = client.get(f"/api/projects/{project_id}/funding-review")
    assert latest.json()["id"] == second_body["id"]
    assert latest.json()["bound_rules"] == []

    history = client.get(f"/api/projects/{project_id}/funding-reviews")
    assert history.status_code == 200
    ids = [item["id"] for item in history.json()]
    assert ids[0] == second_body["id"]
    assert first_body["id"] in ids


def test_missing_project_category_does_not_fail_rule_005(client):
    uploaded = _upload_policy(client).json()
    candidate = _funding_cap(uploaded, "人文社会科学类")
    _enable(client, uploaded["id"], candidate["id"])
    project_id = _create_project_with_materials(client, "无学科类别")
    body = client.post(f"/api/projects/{project_id}/funding-review").json()
    assert body["status"] == "PASS"
    assert body["finding"]["rule_id"] == "RULE-007"
    assert len(body["bound_rules"]) == 1
    assert body["bound_rules"][0]["category"] == "人文社会科学类"
    assert "不按项目学科类别裁决" in (body["bound_rules"][0]["display_note"] or "")
