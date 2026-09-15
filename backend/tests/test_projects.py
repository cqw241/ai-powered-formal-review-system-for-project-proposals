"""Project CRUD API tests (no cloud calls)."""

from __future__ import annotations


def test_list_empty(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_create_and_get_project(client):
    created = client.post("/api/projects", json={"name": "青年科研创新培育计划-样例"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "青年科研创新培育计划-样例"
    assert body["id"]
    assert body["created_at"].endswith("Z")

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]
    assert listed.json()[0]["created_at"].endswith("Z")

    detail = client.get(f"/api/projects/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == body["name"]
    assert detail.json()["created_at"].endswith("Z")


def test_create_rejects_blank_name(client):
    response = client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422


def test_create_trims_name(client):
    response = client.post("/api/projects", json={"name": "  测试项目  "})
    assert response.status_code == 201
    assert response.json()["name"] == "测试项目"


def test_get_missing_project(client):
    response = client.get("/api/projects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["detail"] == "项目不存在"


def test_health_does_not_require_llm(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "llm_configured" in body
