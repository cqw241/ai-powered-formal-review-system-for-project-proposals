"""W3 workspace wiring: one review runs identity, budget, date, and funding rules."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"


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


def _create_project(client, name: str) -> str:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def _upload(client, project_id: str, filename: str, category: str):
    path = FIXTURES / filename
    with path.open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/materials",
            data={"category": category},
            files={"file": (path.name, handle, "application/pdf")},
        )
    assert response.status_code == 201, response.text
    return response


def _pack(client, name: str, application: str, budget: str, commitment: str) -> str:
    project_id = _create_project(client, name)
    _upload(client, project_id, application, "APPLICATION")
    _upload(client, project_id, budget, "BUDGET")
    _upload(client, project_id, commitment, "COMMITMENT")
    return project_id


def _item(body: dict, rule_code: str) -> dict:
    matches = [item for item in body["items"] if item["rule_code"] == rule_code]
    assert matches, rule_code
    return matches[0]


def _start(client, project_id: str, rule_ids: list[str] | None = None) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": rule_ids or []},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "COMPLETED"
    return body


def test_pkg_a_one_review_passes_name_period_funding_and_cap(client):
    uploaded = _upload_policy(client).json()
    natural = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    project_id = _pack(
        client,
        "PKG-A",
        "A1_项目申报书.pdf",
        "A2_经费预算表.pdf",
        "A3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id, [natural["id"]])
    assert _item(body, "RULE-002")["check_status"] == "PASS"
    assert _item(body, "RULE-003")["check_status"] == "PASS"
    assert _item(body, "RULE-004")["check_status"] == "PASS"
    assert "2027-01-01" in _item(body, "RULE-004")["summary"]
    assert _item(body, "RULE-005")["check_status"] == "PASS"
    assert "300000" in _item(body, "RULE-005")["summary"]
    assert _item(body, "RULE-007")["check_status"] == "PASS"
    assert _item(body, "RULE-010")["check_status"] == "PASS"
    reloaded = client.get(f"/api/projects/{project_id}/reviews/{body['id']}").json()
    assert _item(reloaded, "RULE-004")["check_status"] == "PASS"
    quotes = [item["quote"] for item in _item(reloaded, "RULE-002")["result"]["evidence"]]
    assert any(quotes)


def test_pkg_b_lists_name_diff_over_cap_and_late_dates(client):
    uploaded = _upload_policy(client).json()
    humanities = _enable(
        client, uploaded["id"], _funding_cap(uploaded, "人文社会科学类")["id"]
    ).json()
    project_id = _pack(
        client,
        "PKG-B",
        "B1_项目申报书.pdf",
        "B2_经费预算表.pdf",
        "B3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id, [humanities["id"]])
    name = _item(body, "RULE-002")
    assert name["check_status"] == "FAIL"
    assert name["status"] == "COMPLETED"
    evidence = name["result"]["evidence"]
    assert len(evidence) >= 2
    assert any(item.get("quote") for item in evidence)
    assert any(item.get("page_number") for item in evidence)
    cap = _item(body, "RULE-005")
    assert cap["check_status"] == "FAIL"
    assert "153000" in cap["summary"]
    assert "150000" in cap["summary"]
    period = _item(body, "RULE-004")
    assert period["check_status"] == "FAIL"
    assert "2029-02-28" in period["summary"]
    signing = _item(body, "RULE-010")
    assert signing["check_status"] == "FAIL"
    assert "2026-10-02" in signing["summary"]


def test_pkg_c_budget_sum_and_principal_pass(client):
    project_id = _pack(
        client,
        "PKG-C",
        "C1_项目申报书.pdf",
        "C2_经费预算表.pdf",
        "C3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id)
    assert "RULE-005" not in [item["rule_code"] for item in body["items"]]
    summed = _item(body, "RULE-006")
    assert summed["check_status"] == "PASS"
    assert "286000" in summed["summary"]
    principal = _item(body, "RULE-003")
    assert principal["check_status"] == "PASS"
    assert "顾言澈" in principal["summary"]


def test_budget_sum_gap_and_unclear_category_are_distinguished(client):
    uploaded = _upload_policy(client).json()
    natural = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    gap_id = _pack(
        client,
        "合计差额",
        "A1_项目申报书.pdf",
        "A2_经费预算表_合计差额2元.pdf",
        "A3_科研诚信与合规承诺书.pdf",
    )
    gap = _start(client, gap_id)
    assert _item(gap, "RULE-006")["check_status"] == "FAIL"
    assert "2 元" in _item(gap, "RULE-006")["summary"]

    missing_id = _create_project(client, "类别缺失")
    _upload(client, missing_id, "A1_项目申报书_类别缺失.pdf", "APPLICATION")
    _upload(client, missing_id, "A2_经费预算表.pdf", "BUDGET")
    _upload(client, missing_id, "A3_科研诚信与合规承诺书.pdf", "COMMITMENT")
    missing = _start(client, missing_id, [natural["id"]])
    assert _item(missing, "RULE-005")["check_status"] == "NEED_HUMAN_REVIEW"
    assert "项目类别" in _item(missing, "RULE-005")["summary"]
