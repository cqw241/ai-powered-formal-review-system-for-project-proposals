"""B10 workspace wiring: RULE-001/008/009 run in the same review task."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def _create_project(client, name: str) -> str:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def _upload(client, project_id: str, filename: str, category: str):
    path = FIXTURES / filename
    response = client.post(
        f"/api/projects/{project_id}/materials",
        data={"category": category},
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _pack(client, name: str, files: list[tuple[str, str]]) -> str:
    project_id = _create_project(client, name)
    for filename, category in files:
        _upload(client, project_id, filename, category)
    return project_id


def _item(body: dict, rule_code: str) -> dict:
    matches = [item for item in body["items"] if item["rule_code"] == rule_code]
    assert matches, rule_code
    return matches[0]


def _start(client, project_id: str) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": []},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "COMPLETED"
    return body


def test_complete_pack_without_commitment_is_missing_required_file(client):
    project_id = _pack(
        client,
        "CASE-002",
        [
            ("A1_项目申报书.pdf", "APPLICATION"),
            ("A2_经费预算表.pdf", "BUDGET"),
        ],
    )
    body = _start(client, project_id)
    item = _item(body, "RULE-001")
    assert item["check_status"] == "FAIL"
    assert item["status"] == "COMPLETED"
    assert "承诺书" in item["summary"]
    assert item["result"]["evidence"]


def test_pkg_c_98000_device_missing_necessity_attachment(client):
    project_id = _pack(
        client,
        "PKG-C",
        [
            ("C1_项目申报书.pdf", "APPLICATION"),
            ("C2_经费预算表.pdf", "BUDGET"),
            ("C3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    body = _start(client, project_id)
    item = _item(body, "RULE-008")
    assert item["check_status"] == "FAIL"
    assert item["status"] == "COMPLETED"
    assert "98000" in item["summary"]
    ethics = _item(body, "RULE-009")
    assert ethics["check_status"] == "NEED_HUMAN_REVIEW"
    assert ethics["status"] == "PENDING_CONFIRMATION"
    assert "依据" in ethics["summary"]
    assert ethics["result"]["data"]["pending_questions"]
    assert any(row.get("quote") for row in ethics["result"]["evidence"])


def test_equipment_attachment_file_marks_rule_008_pass(client):
    project_id = _pack(
        client,
        "PKG-C-with-attachment",
        [
            ("C1_项目申报书.pdf", "APPLICATION"),
            ("C2_经费预算表.pdf", "BUDGET"),
            ("C3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    path = FIXTURES / "C2_经费预算表.pdf"
    uploaded = client.post(
        f"/api/projects/{project_id}/materials",
        data={"category": "OTHER"},
        files={"file": ("设备必要性说明.pdf", path.read_bytes(), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = _start(client, project_id)
    item = _item(body, "RULE-008")
    assert item["check_status"] == "PASS"
    assert item["status"] == "COMPLETED"
    assert "已提供" in item["summary"]


def test_pkg_a_conditional_attachment_not_applicable(client):
    project_id = _pack(
        client,
        "PKG-A",
        [
            ("A1_项目申报书.pdf", "APPLICATION"),
            ("A2_经费预算表.pdf", "BUDGET"),
            ("A3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    body = _start(client, project_id)
    required = _item(body, "RULE-001")
    assert required["check_status"] == "PASS"
    equipment = _item(body, "RULE-008")
    assert equipment["check_status"] == "NOT_APPLICABLE"
    assert equipment["status"] == "COMPLETED"
    assert "不适用" in equipment["summary"]
    ethics = _item(body, "RULE-009")
    assert ethics["check_status"] == "PASS"
    reloaded = client.get(f"/api/projects/{project_id}/reviews/{body['id']}").json()
    assert _item(reloaded, "RULE-008")["check_status"] == "NOT_APPLICABLE"
