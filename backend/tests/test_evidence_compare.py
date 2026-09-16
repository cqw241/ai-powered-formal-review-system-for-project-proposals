"""B11: generic issue detail, dual-document compare, scan-page highlight."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.review_contract import ReviewCheckStatus, ReviewEvidence, ReviewEvidenceBBox, RuleExecutionResult
from app.services.date_extract import extract_signing_date_from_pdf
from app.services.evidence_compare import (
    bbox_to_percent,
    build_compare_view,
    expand_recognition_bbox,
    highlight_display_rect,
    list_labeled_funding_fields,
)
from app.services.identity_extract import extract_principal_from_pdf, extract_project_name_from_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"
POLICY_PDF = FIXTURES / "POL_申报指南_开发批次规范.pdf"

B2_BLURRED = FIXTURES / "B2_经费预算表_标题模糊.pdf"
B3_HANDWRITTEN = FIXTURES / "B3_承诺书_手写负责人.pdf"
B3_OCCLUDED = FIXTURES / "B3_承诺书_签署日期遮挡.pdf"
A1_DUAL = FIXTURES / "A1_项目申报书_总经费与申请经费.pdf"


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
    return response.json()


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


def _image_rect(path: Path) -> tuple[float, float, float, float]:
    doc = pymupdf.open(path)
    try:
        page = doc.load_page(0)
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 1:
                x0, y0, x1, y1 = block["bbox"]
                return float(x0), float(y0), float(x1), float(y1)
    finally:
        doc.close()
    raise AssertionError(f"no image block in {path.name}")


def _to_contract_bbox(bbox) -> ReviewEvidenceBBox:
    return ReviewEvidenceBBox(
        x0=bbox.x0,
        y0=bbox.y0,
        x1=bbox.x1,
        y1=bbox.y1,
        page_width=bbox.page_width,
        page_height=bbox.page_height,
    )


def test_zoom_keeps_highlight_aligned_on_scan_pages():
    extracted = extract_project_name_from_pdf(B2_BLURRED)
    assert extracted.bbox is not None
    expanded = expand_recognition_bbox(B2_BLURRED, extracted.page_number or 1, extracted.bbox)
    assert expanded is not None
    percent = bbox_to_percent(expanded)
    for zoom in (1.0, 1.5, 2.0, 0.75):
        displayed_w = expanded.page_width * zoom
        displayed_h = expanded.page_height * zoom
        rect = highlight_display_rect(expanded, displayed_width=displayed_w, displayed_height=displayed_h)
        assert abs(rect["left"] - expanded.x0 * zoom) < 1e-6
        assert abs(rect["top"] - expanded.y0 * zoom) < 1e-6
        assert abs(rect["width"] - (expanded.x1 - expanded.x0) * zoom) < 1e-6
        assert abs(rect["height"] - (expanded.y1 - expanded.y0) * zoom) < 1e-6
        assert abs(percent["left"] / 100.0 * displayed_w - rect["left"]) < 1e-6


def test_scan_blurred_title_highlight_covers_image():
    extracted = extract_project_name_from_pdf(B2_BLURRED)
    assert extracted.page_number == 1
    assert extracted.bbox is not None
    expanded = expand_recognition_bbox(B2_BLURRED, 1, extracted.bbox)
    assert expanded is not None
    image = _image_rect(B2_BLURRED)
    assert expanded.x0 <= image[0] + 1
    assert expanded.y0 <= extracted.bbox.y0 + 1
    assert expanded.x1 >= image[2] - 1
    assert expanded.y1 >= image[3] - 1


def test_scan_handwritten_principal_highlight_covers_image():
    extracted = extract_principal_from_pdf(B3_HANDWRITTEN)
    assert extracted.page_number == 1
    assert extracted.bbox is not None
    expanded = expand_recognition_bbox(B3_HANDWRITTEN, 1, extracted.bbox)
    assert expanded is not None
    image = _image_rect(B3_HANDWRITTEN)
    assert expanded.y1 >= image[3] - 1
    assert expanded.x1 >= image[2] - 1
    assert expanded.y0 <= extracted.bbox.y0 + 1


def test_scan_occluded_signing_date_has_locatable_region():
    extracted = extract_signing_date_from_pdf(B3_OCCLUDED)
    assert extracted.page_number == 1
    assert extracted.bbox is not None
    expanded = expand_recognition_bbox(B3_OCCLUDED, 1, extracted.bbox)
    assert expanded is not None
    assert expanded.page_width == extracted.bbox.page_width
    assert expanded.x1 > expanded.x0
    assert expanded.y1 > expanded.y0
    assert expanded.x0 <= extracted.bbox.x0 + 1
    assert expanded.y1 >= extracted.bbox.y1 - 1


def test_application_and_total_funding_are_labeled_separately():
    fields = list_labeled_funding_fields(A1_DUAL)
    by_name = {item.field_name: item for item in fields}
    assert "申请经费" in by_name
    assert "项目总经费" in by_name
    assert by_name["申请经费"].field_kind == "application_funding"
    assert by_name["项目总经费"].field_kind == "total_funding"
    assert "15" in (by_name["申请经费"].raw_value or "")
    assert "20" in (by_name["项目总经费"].raw_value or "")
    assert by_name["申请经费"].unit == "万元"
    assert by_name["项目总经费"].unit == "万元"
    assert by_name["申请经费"].field_name != by_name["项目总经费"].field_name


def test_compare_view_from_existing_scan_evidence_is_openable():
    extracted = extract_project_name_from_pdf(B2_BLURRED)
    evidence = ReviewEvidence(
        material_id="scan-budget",
        category="BUDGET",
        original_filename=B2_BLURRED.name,
        field_name="项目名称",
        raw_value=extracted.raw_value,
        page_number=extracted.page_number,
        quote=extracted.quote,
        bbox=_to_contract_bbox(extracted.bbox) if extracted.bbox else None,
        reliable=extracted.reliable,
        reason=extracted.reason,
    )
    result = RuleExecutionResult(
        status=ReviewCheckStatus.NEED_HUMAN_REVIEW,
        summary=extracted.reason or "需人工确认",
        evidence=[evidence],
        data={"rule_code": "RULE-002", "check_field": "项目名称"},
    )
    view = build_compare_view(result, material_paths={"scan-budget": B2_BLURRED})
    assert view.check_field == "项目名称"
    assert view.sides[0].openable is True
    assert view.sides[0].field_name == "项目名称"
    assert view.sides[0].bbox is not None
    image = _image_rect(B2_BLURRED)
    assert view.sides[0].bbox.y1 >= image[3] - 1


def test_name_amount_date_issues_open_originals_with_compare_fields(client):
    uploaded = _upload_policy(client).json()
    humanities = _enable(
        client, uploaded["id"], _funding_cap(uploaded, "人文社会科学类")["id"]
    ).json()
    project_id = _pack(
        client,
        "B11-PKG-B",
        "B1_项目申报书.pdf",
        "B2_经费预算表.pdf",
        "B3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id, [humanities["id"]])

    name = _item(body, "RULE-002")
    assert name["check_status"] == "FAIL"
    name_compare = name["compare"]
    assert name_compare["check_field"] == "项目名称"
    assert name_compare["difference"]
    assert len(name_compare["sides"]) >= 2
    for side in name_compare["sides"]:
        assert side["field_name"] == "项目名称"
        assert side["raw_value"]
        assert side["openable"] is True
        assert side["material_id"]
        assert side["page_number"] >= 1
        assert side["unit"] is None

    amount = _item(body, "RULE-007")
    amount_compare = amount["compare"]
    assert amount_compare["check_field"] == "申请经费"
    assert len(amount_compare["sides"]) >= 2
    field_names = {side["field_name"] for side in amount_compare["sides"]}
    assert "申请经费" in field_names
    assert "申请总额" in field_names
    for side in amount_compare["sides"]:
        if side["raw_value"]:
            assert side["openable"] is True
            assert side["page_number"] >= 1
            assert side["unit"] in {"元", "万元", "千元"}
        assert side["field_kind"] == "application_funding"
        assert "总经费" not in (side["field_name"] or "")

    cap = _item(body, "RULE-005")
    assert cap["check_status"] == "FAIL"
    cap_compare = cap["compare"]
    assert cap_compare["difference_yuan"] in {3000, 3000.0} or cap_compare["difference"]
    cap_fields = {side["field_name"] for side in cap_compare["sides"]}
    assert "申请经费" in cap_fields or "申请总额" in cap_fields
    assert "经费上限" in cap_fields

    period = _item(body, "RULE-004")
    assert period["check_status"] == "FAIL"
    period_compare = period["compare"]
    assert period_compare["check_field"] == "执行期"
    assert any(side["openable"] and side["bbox"] for side in period_compare["sides"])
    assert any(side["field_name"] in {"开始日期", "结束日期"} for side in period_compare["sides"])

    signing = _item(body, "RULE-010")
    assert signing["check_status"] == "FAIL"
    signing_compare = signing["compare"]
    assert signing_compare["check_field"] == "签署日期"
    assert signing_compare["sides"][0]["openable"] is True
    assert signing_compare["sides"][0]["page_number"] >= 1
    assert signing_compare["sides"][0]["bbox"] is not None


def test_pkg_a_amount_originals_remain_openable(client):
    project_id = _pack(
        client,
        "B11-PKG-A",
        "A1_项目申报书.pdf",
        "A2_经费预算表.pdf",
        "A3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id)
    funding = _item(body, "RULE-007")
    assert funding["check_status"] == "PASS"
    compare = funding["compare"]
    names = [side["field_name"] for side in compare["sides"]]
    assert "申请经费" in names
    assert "申请总额" in names
    for side in compare["sides"]:
        assert side["openable"] is True
        assert side["bbox"] is not None
        assert side["unit"] in {"万元", "元"}
        assert side["field_kind"] == "application_funding"


def test_dual_funding_fixture_marks_application_and_total_separately(client):
    project_id = _pack(
        client,
        "B11-总经费与申请经费",
        "A1_项目申报书_总经费与申请经费.pdf",
        "A2_经费预算表_申请总额20万元.pdf",
        "A3_科研诚信与合规承诺书.pdf",
    )
    body = _start(client, project_id)
    funding = _item(body, "RULE-007")
    compare = funding["compare"]
    labeled = {item["field_name"]: item for item in compare["funding_fields"]}
    assert "申请经费" in labeled
    assert "项目总经费" in labeled
    assert labeled["申请经费"]["field_kind"] == "application_funding"
    assert labeled["项目总经费"]["field_kind"] == "total_funding"
    assert labeled["申请经费"]["raw_value"] != labeled["项目总经费"]["raw_value"]
    side_kinds = {side["field_kind"] for side in compare["sides"] if side.get("field_kind")}
    assert "application_funding" in side_kinds or any(
        side["field_name"] in {"申请经费", "申请总额", "项目总经费"} for side in compare["sides"]
    )


def test_scan_variants_in_review_keep_locatable_highlights(client):
    project_id = _pack(
        client,
        "B11-扫描页",
        "B1_项目申报书.pdf",
        "B2_经费预算表_标题模糊.pdf",
        "B3_承诺书_手写负责人.pdf",
    )
    body = _start(client, project_id)

    name = _item(body, "RULE-002")
    assert name["check_status"] == "NEED_HUMAN_REVIEW"
    budget_side = next(side for side in name["compare"]["sides"] if side["category"] == "BUDGET")
    assert budget_side["openable"] is True
    assert budget_side["bbox"] is not None
    image = _image_rect(B2_BLURRED)
    assert budget_side["bbox"]["y1"] >= image[3] - 1

    principal = _item(body, "RULE-003")
    assert principal["check_status"] == "NEED_HUMAN_REVIEW"
    commitment_side = next(
        side for side in principal["compare"]["sides"] if side["category"] == "COMMITMENT"
    )
    assert commitment_side["openable"] is True
    assert commitment_side["bbox"] is not None
    hand_image = _image_rect(B3_HANDWRITTEN)
    assert commitment_side["bbox"]["y1"] >= hand_image[3] - 1

    occluded_id = _create_project(client, "B11-签署遮挡")
    _upload(client, occluded_id, "B1_项目申报书.pdf", "APPLICATION")
    _upload(client, occluded_id, "B2_经费预算表.pdf", "BUDGET")
    _upload(client, occluded_id, "B3_承诺书_签署日期遮挡.pdf", "COMMITMENT")
    occluded = _start(client, occluded_id)
    signing = _item(occluded, "RULE-010")
    assert signing["check_status"] == "NEED_HUMAN_REVIEW"
    side = signing["compare"]["sides"][0]
    assert side["openable"] is True
    assert side["field_name"] == "签署日期"
    assert side["bbox"] is not None
    assert side["raw_value"]
