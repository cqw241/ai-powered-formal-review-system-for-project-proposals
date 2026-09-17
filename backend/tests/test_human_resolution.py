"""B12 human confirmation, field correction, and not-applicable persistence."""

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


def _start(client, project_id: str, rule_ids: list[str] | None = None) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"rule_ids": rule_ids or []},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "COMPLETED"
    return body


def _decide(client, project_id: str, task_id: str, item_id: str, payload: dict):
    return client.post(
        f"/api/projects/{project_id}/reviews/{task_id}/items/{item_id}/human-decisions",
        json=payload,
    )


def _evidence(item: dict, *, category: str, field_name: str) -> dict:
    matches = [
        row
        for row in (item.get("result") or {}).get("evidence") or []
        if row.get("category") == category and row.get("field_name") == field_name
    ]
    assert matches, (category, field_name, item.get("result"))
    return matches[0]


def test_principal_correction_recomputes_rule_003_and_keeps_machine_result(client):
    project_id = _pack(
        client,
        "B12 负责人修正",
        [
            ("A1_项目申报书.pdf", "APPLICATION"),
            ("A2_经费预算表_负责人林书彦.pdf", "BUDGET"),
            ("A3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    started = _start(client, project_id)
    item = _item(started, "RULE-003")
    assert item["check_status"] == "FAIL"
    assert "林书彦" in item["summary"]
    budget = _evidence(item, category="BUDGET", field_name="项目负责人")
    assert budget["raw_value"] == "林书彦"

    response = _decide(
        client,
        project_id,
        started["id"],
        item["id"],
        {
            "action": "CORRECT_FIELD",
            "operator": "陈秘书",
            "note": "预算表负责人应为林书言",
            "field_name": "项目负责人",
            "material_id": budget["material_id"],
            "corrected_value": "林书言",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    updated = _item(body, "RULE-003")
    assert updated["check_status"] == "PASS"
    assert updated["status"] == "COMPLETED"
    assert "林书言" in updated["summary"]
    assert "林书彦" not in updated["summary"]
    assert updated["original_result"]["status"] == "FAIL"
    assert "林书彦" in updated["original_result"]["summary"]
    history = updated["human_decisions"]
    assert len(history) == 1
    assert history[0]["action"] == "CORRECT_FIELD"
    assert history[0]["operator"] == "陈秘书"
    assert history[0]["note"] == "预算表负责人应为林书言"
    assert history[0]["original_value"] == "林书彦"
    assert history[0]["corrected_value"] == "林书言"
    assert "RULE-003" in history[0]["affected_rule_codes"]

    reloaded = client.get(f"/api/projects/{project_id}/reviews/{started['id']}")
    assert reloaded.status_code == 200
    persisted = _item(reloaded.json(), "RULE-003")
    assert persisted["check_status"] == "PASS"
    assert persisted["original_result"]["status"] == "FAIL"
    assert persisted["human_decisions"][0]["operator"] == "陈秘书"


def test_funding_correction_recomputes_amount_rules_and_keeps_machine_result(client):
    uploaded = _upload_policy(client).json()
    natural = _enable(client, uploaded["id"], _funding_cap(uploaded, "自然科学类")["id"]).json()
    project_id = _pack(
        client,
        "B12 经费修正",
        [
            ("A1_项目申报书.pdf", "APPLICATION"),
            ("A2_经费预算表_320000.pdf", "BUDGET"),
            ("A3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    started = _start(client, project_id, [natural["id"]])
    rule007 = _item(started, "RULE-007")
    rule005 = _item(started, "RULE-005")
    rule006 = _item(started, "RULE-006")
    assert rule007["check_status"] == "FAIL"
    assert rule005["check_status"] == "FAIL"
    assert "320000" in (rule005["summary"] + rule007["summary"]).replace(",", "")
    original006 = rule006["check_status"]
    budget = next(
        row
        for row in rule007["result"]["evidence"]
        if row.get("category") == "BUDGET" and row.get("material_id")
    )

    before_funding = client.get(f"/api/projects/{project_id}/funding-reviews").json()
    assert len(before_funding) == 1

    response = _decide(
        client,
        project_id,
        started["id"],
        rule007["id"],
        {
            "action": "CORRECT_FIELD",
            "operator": "陈秘书",
            "note": "预算申请总额应为 300000 元",
            "field_name": budget["field_name"],
            "material_id": budget["material_id"],
            "corrected_value": "300000元",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    updated007 = _item(body, "RULE-007")
    updated005 = _item(body, "RULE-005")
    updated006 = _item(body, "RULE-006")
    assert updated007["check_status"] == "PASS"
    assert updated007["original_result"]["status"] == "FAIL"
    assert updated005["check_status"] == "PASS"
    assert updated005["original_result"]["status"] == "FAIL"
    assert updated006["check_status"] in {"PASS", original006}
    assert any(row["action"] == "CORRECT_FIELD" for row in updated007["human_decisions"])
    assert any(row["action"] == "CORRECT_FIELD" for row in updated005["human_decisions"])
    assert any(row["action"] == "CORRECT_FIELD" for row in updated006["human_decisions"])
    after_funding = client.get(f"/api/projects/{project_id}/funding-reviews").json()
    assert len(after_funding) == 1
    assert after_funding[0]["id"] == before_funding[0]["id"]

    reloaded = client.get(f"/api/projects/{project_id}/reviews/{started['id']}").json()
    assert _item(reloaded, "RULE-007")["check_status"] == "PASS"
    assert _item(reloaded, "RULE-007")["original_result"]["status"] == "FAIL"
    assert _item(reloaded, "RULE-005")["check_status"] == "PASS"


def test_confirm_and_not_applicable_survive_reload(client):
    project_id = _pack(
        client,
        "B12 确认与不适用",
        [
            ("C1_项目申报书.pdf", "APPLICATION"),
            ("C2_经费预算表.pdf", "BUDGET"),
            ("C3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    started = _start(client, project_id)
    ethics = _item(started, "RULE-009")
    equipment = _item(started, "RULE-008")
    assert ethics["check_status"] == "NEED_HUMAN_REVIEW"
    assert ethics["status"] == "PENDING_CONFIRMATION"
    assert equipment["check_status"] == "FAIL"

    confirmed = _decide(
        client,
        project_id,
        started["id"],
        ethics["id"],
        {
            "action": "CONFIRM",
            "operator": "陈秘书",
            "note": "伦理适用性待补充授权，确认该问题",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    ethics_after = _item(confirmed.json(), "RULE-009")
    assert ethics_after["status"] == "COMPLETED"
    assert ethics_after["check_status"] == "NEED_HUMAN_REVIEW"
    assert ethics_after["original_result"]["status"] == "NEED_HUMAN_REVIEW"
    assert ethics_after["human_decisions"][0]["action"] == "CONFIRM"

    marked = _decide(
        client,
        project_id,
        started["id"],
        equipment["id"],
        {
            "action": "MARK_NOT_APPLICABLE",
            "operator": "陈秘书",
            "note": "本项目设备必要性说明改由院内备案，本条不适用",
        },
    )
    assert marked.status_code == 200, marked.text
    equipment_after = _item(marked.json(), "RULE-008")
    assert equipment_after["check_status"] == "NOT_APPLICABLE"
    assert equipment_after["status"] == "COMPLETED"
    assert equipment_after["original_result"]["status"] == "FAIL"
    assert "人工标记不适用" in equipment_after["summary"]

    reloaded = client.get(f"/api/projects/{project_id}/reviews/{started['id']}").json()
    ethics_reloaded = _item(reloaded, "RULE-009")
    equipment_reloaded = _item(reloaded, "RULE-008")
    assert ethics_reloaded["status"] == "COMPLETED"
    assert ethics_reloaded["human_decisions"][0]["operator"] == "陈秘书"
    assert ethics_reloaded["human_decisions"][0]["note"] == "伦理适用性待补充授权，确认该问题"
    assert equipment_reloaded["check_status"] == "NOT_APPLICABLE"
    assert equipment_reloaded["original_result"]["status"] == "FAIL"
    assert equipment_reloaded["human_decisions"][0]["action"] == "MARK_NOT_APPLICABLE"

    materials = _item(reloaded, "RULE-001")
    assert materials["check_status"] == "PASS"
    assert not materials["human_decisions"]


def test_correction_rejects_unparseable_amount_without_overwriting_result(client):
    project_id = _pack(
        client,
        "B12 金额无法解析",
        [
            ("A1_项目申报书.pdf", "APPLICATION"),
            ("A2_经费预算表_320000.pdf", "BUDGET"),
            ("A3_科研诚信与合规承诺书.pdf", "COMMITMENT"),
        ],
    )
    started = _start(client, project_id)
    item = _item(started, "RULE-007")
    budget = next(
        row
        for row in item["result"]["evidence"]
        if row.get("category") == "BUDGET" and row.get("material_id")
    )
    response = _decide(
        client,
        project_id,
        started["id"],
        item["id"],
        {
            "action": "CORRECT_FIELD",
            "operator": "陈秘书",
            "note": "尝试填入无法解析的金额",
            "field_name": budget["field_name"],
            "material_id": budget["material_id"],
            "corrected_value": "不是金额",
        },
    )
    assert response.status_code == 400
    assert "无法解析" in response.json()["detail"]
    reloaded = _item(client.get(f"/api/projects/{project_id}/reviews/{started['id']}").json(), "RULE-007")
    assert reloaded["check_status"] == "FAIL"
    assert reloaded["human_decisions"] == []
