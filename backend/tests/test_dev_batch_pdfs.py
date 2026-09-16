"""Key-field extraction checks for development-batch PDFs (PKG-B/C and variants)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def _text(name: str) -> str:
    path = FIXTURES / name
    assert path.is_file(), path
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() or "" for page in doc)


def test_pkg_b_pdfs():
    b1 = _text("B1_项目申报书.pdf")
    assert "认知负荷与反馈机制研究" in b1
    assert "人文社会科学类" in b1
    assert "14.80万元" in b1
    assert "2029-02-28" in b1
    b2 = _text("B2_经费预算表.pdf")
    assert "153000" in b2
    b3 = _text("B3_科研诚信与合规承诺书.pdf")
    assert "生成式人工智能辅助大学生学术写作反馈机制研究" in b3
    assert "认知负荷与" not in b3
    assert "2026-10-02" in b3


def test_pkg_c_pdfs():
    c1 = _text("C1_项目申报书.pdf")
    assert "顾言澈" in c1
    assert "28.60万元" in c1
    assert "去标识化历史脑电" in c1
    c2 = _text("C2_经费预算表.pdf")
    assert "286000" in c2
    assert "98000" in c2
    c3 = _text("C3_科研诚信与合规承诺书.pdf")
    assert "2026-09-26" in c3
    assert "设备必要性说明" in c3


def test_variant_pdfs_key_signals():
    assert "林书彦" in _text("A2_经费预算表_负责人林书彦.pdf")
    assert "约两年" in _text("A1_项目申报书_周期约两年.pdf")
    missing_cat = _text("A1_项目申报书_类别缺失.pdf")
    assert "18.00万元" in missing_cat
    assert "自然科学类" not in missing_cat
    assert "人文社会科学类" not in missing_cat
    delta = _text("A2_经费预算表_合计差额2元.pdf")
    assert "149998" in delta.replace(",", "") or "149,998" in delta
    assert "150000" in delta.replace(",", "") or "150,000" in delta
    assert "配套经费" in _text("A2_经费预算表_配套经费口径不清.pdf")
    dual = _text("A1_项目申报书_总经费与申请经费.pdf")
    assert "项目总经费" in dual and "申请经费" in dual
    assert "200000" in _text("A2_经费预算表_申请总额20万元.pdf")
    assert "2套共90000" in _text("C2_经费预算表_设备无单价.pdf")
    assert "招募20名受试者" in _text("A1_项目申报书_招募受试者.pdf")
    assert "2026-09-2" in _text("B3_承诺书_签署日期遮挡.pdf")


def test_visual_boundary_variants_omit_native_target():
    blur = _text("B2_经费预算表_标题模糊.pdf")
    assert "认知负荷与反馈机制研究" not in blur
    hand = _text("B3_承诺书_手写负责人.pdf")
    assert "周清岚" not in hand
    assert "周清风" not in hand
