#!/usr/bin/env python3
"""Build digital (and a few visual-boundary) PDFs for the development batch.

Run from repo root (Pillow is only needed for three visual-boundary pages;
system Python + the backend venv's PyMuPDF is enough):

    PYTHONPATH="$HOME/.venvs/guizheng-ai-backend/lib/python3.12/site-packages" \\
      python3 docs/kickoff/development_batch/build_pdfs.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXTURES = REPO / "backend" / "fixtures" / "pdfs"
VARIANTS = HERE / "variants"

PAGE = pymupdf.paper_rect("a4")
LEFT, RIGHT, TOP = 36.0, 36.0, 28.0
FONT = "china-s"
CJK_TTC = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
BATCH = "AI-EVAL-EC-2026-09"


def _cjk_font(size: int) -> ImageFont.FreeTypeFont:
    for index in (2, 0, 1, 3):
        try:
            font = ImageFont.truetype(CJK_TTC, size=size, index=index)
            if font.getmask("中").getbbox():
                return font
        except OSError:
            continue
    return ImageFont.load_default()


def _new() -> pymupdf.Document:
    return pymupdf.open()


def _page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE.width, height=PAGE.height)


def _header(page: pymupdf.Page, code: str, file_label: str) -> float:
    page.insert_text((LEFT, TOP + 8), f"{BATCH}  {code}", fontname="helv", fontsize=8)
    page.insert_text(
        (PAGE.width - RIGHT - 88, TOP + 8),
        "第 1 页 / 共 1 页",
        fontname=FONT,
        fontsize=9,
    )
    page.insert_text((LEFT, TOP + 28), file_label, fontname=FONT, fontsize=13)
    y = TOP + 40
    page.draw_line(pymupdf.Point(LEFT, y), pymupdf.Point(PAGE.width - RIGHT, y), width=0.6)
    return y + 16


def _textbox(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, size: float = 10.5, align: int = 0) -> float:
    leftover = page.insert_textbox(rect, text, fontname=FONT, fontsize=size, align=align)
    if leftover < 0:
        raise RuntimeError(f"textbox overflow: {text[:40]!r}")
    return leftover


def _kv(page: pymupdf.Page, y: float, rows: list[tuple[str, str]]) -> float:
    label_w = 88
    row_h = 22
    x0, x1 = LEFT, PAGE.width - RIGHT
    for label, value in rows:
        page.draw_rect(pymupdf.Rect(x0, y, x1, y + row_h), color=(0.75, 0.78, 0.82), width=0.4)
        page.draw_rect(pymupdf.Rect(x0, y, x0 + label_w, y + row_h), fill=(0.93, 0.95, 0.97), width=0)
        page.draw_rect(pymupdf.Rect(x0, y, x0 + label_w, y + row_h), color=(0.75, 0.78, 0.82), width=0.4)
        _textbox(page, pymupdf.Rect(x0 + 4, y + 4, x0 + label_w - 4, y + row_h - 2), label, size=9)
        _textbox(page, pymupdf.Rect(x0 + label_w + 6, y + 4, x1 - 4, y + row_h - 2), value, size=10)
        y += row_h
    return y + 10


def _section(page: pymupdf.Page, y: float, title: str) -> float:
    page.insert_text((LEFT, y + 12), title, fontname=FONT, fontsize=11)
    return y + 20


def _para(page: pymupdf.Page, y: float, text: str, *, height: float = 72) -> float:
    rect = pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + height)
    _textbox(page, rect, text, size=10)
    return y + height + 6


def _table(page: pymupdf.Page, y: float, headers: list[str], rows: list[list[str]], widths: list[float]) -> float:
    x0 = LEFT
    header_h, row_h = 20, 28
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    def draw_row(yy: float, cells: list[str], fill: tuple[float, float, float] | None, size: float) -> None:
        for i, cell in enumerate(cells):
            rect = pymupdf.Rect(xs[i], yy, xs[i + 1], yy + (header_h if fill else row_h))
            if fill:
                page.draw_rect(rect, fill=fill, width=0)
            page.draw_rect(rect, color=(0.7, 0.73, 0.78), width=0.4)
            _textbox(page, pymupdf.Rect(rect.x0 + 3, rect.y0 + 3, rect.x1 - 3, rect.y1 - 2), cell, size=size)

    draw_row(y, headers, (0.90, 0.93, 0.96), 9)
    y += header_h
    for row in rows:
        draw_row(y, row, None, 9)
        y += row_h
    return y + 8


def _save(doc: pymupdf.Document, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    dest = FIXTURES / path.name
    shutil.copy2(path, dest)


def _insert_png(page: pymupdf.Page, rect: pymupdf.Rect, image: Image.Image) -> None:
    buf = image.convert("RGB")
    tmp = VARIANTS / "_tmp_img.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    buf.save(tmp)
    page.insert_image(rect, filename=str(tmp))
    tmp.unlink(missing_ok=True)


def application(
    path: Path,
    *,
    code: str,
    file_label: str,
    title: str,
    fields: list[tuple[str, str]],
    sections: list[tuple[str, str]],
) -> None:
    doc = _new()
    page = _page(doc)
    y = _header(page, code, file_label)
    _textbox(page, pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + 32), title, size=13)
    y += 36
    y = _kv(page, y, fields)
    for heading, body in sections:
        y = _section(page, y, heading)
        y = _para(page, y, body, height=70 if len(body) > 80 else 48)
    _save(doc, path)


def budget(
    path: Path,
    *,
    code: str,
    file_label: str,
    title: str,
    fields: list[tuple[str, str]],
    headers: list[str],
    rows: list[list[str]],
    widths: list[float],
    note: str,
    extra_rows: list[list[str]] | None = None,
    extra_note: str | None = None,
) -> None:
    doc = _new()
    page = _page(doc)
    y = _header(page, code, file_label)
    _textbox(page, pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + 28), title, size=12)
    y += 32
    y = _kv(page, y, fields)
    y = _section(page, y, "一、科目预算")
    y = _table(page, y, headers, rows, widths)
    if extra_rows:
        y = _table(page, y, headers, extra_rows, widths)
    y = _para(page, y, note, height=56)
    if extra_note:
        _para(page, y, extra_note, height=40)
    _save(doc, path)


def commitment(
    path: Path,
    *,
    code: str,
    file_label: str,
    fields: list[tuple[str, str]],
    body: str,
    sign_name: str,
    sign_date: str,
    extra: str | None = None,
) -> None:
    doc = _new()
    page = _page(doc)
    y = _header(page, code, file_label)
    page.insert_text((LEFT, y + 14), "科研诚信与合规承诺书", fontname=FONT, fontsize=14)
    y += 24
    y = _kv(page, y, fields)
    y = _section(page, y, "一、基本承诺")
    y = _para(page, y, body, height=90)
    if extra:
        y = _para(page, y, extra, height=48)
    y = _section(page, y, "二、签署")
    y = _kv(page, y, [("项目负责人", sign_name), ("签署日期", sign_date)])
    _save(doc, path)


def build_pkg_b() -> None:
    title = "生成式人工智能辅助大学生学术写作的认知负荷与反馈机制研究"
    application(
        HERE / "PKG-B" / "B1_项目申报书.pdf",
        code="B1",
        file_label="文件 B1  项目申报书",
        title=title,
        fields=[
            ("项目类别", "人文社会科学类"),
            ("项目负责人", "周清岚"),
            ("所在单位", "教育学院"),
            ("执行期", "2027-03-01 至 2029-02-28"),
            ("申请经费", "14.80万元"),
        ],
        sections=[
            (
                "一、项目摘要",
                "本项目拟研究生成式人工智能辅助学术写作过程中不同反馈方式对大学生认知负荷、修改行为和写作质量的影响。计划采用课堂观察、学习日志、匿名问卷与文本版本分析等方法，形成可复用的教学反馈框架。",
            ),
            (
                "二、研究对象与数据",
                "研究拟在两门本科课程中开展教学观察，使用课程作业、学习日志和问卷。问卷涉及学习体验、压力感受和AI工具使用习惯，不采集身份证号、联系方式等直接身份标识。具体是否构成个人敏感信息及是否需要伦理审批，拟在实施前由学校伦理与数据管理部门确认。",
            ),
        ],
    )
    budget(
        HERE / "PKG-B" / "B2_经费预算表.pdf",
        code="B2",
        file_label="文件 B2  经费预算表",
        title=title,
        fields=[("项目负责人", "周清岚"), ("申请总额", "153000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[
            ["数据采集与调研费", "40,000", "问卷、访谈整理与调研"],
            ["软件与云服务费", "30,000", "文本分析与模型服务"],
            ["差旅/会议费", "28,000", "调研与学术交流"],
            ["出版/文献费", "20,000", "文献和成果出版"],
            ["劳务费", "35,000", "学生助研"],
            ["合计", "153,000", "预算科目合计"],
        ],
        widths=[150, 90, 283],
        note="本表申请总额为 153000 元。科目金额之和等于申请总额。",
    )
    commitment(
        HERE / "PKG-B" / "B3_科研诚信与合规承诺书.pdf",
        code="B3",
        file_label="文件 B3  科研诚信与合规承诺书",
        fields=[
            ("项目名称", "生成式人工智能辅助大学生学术写作反馈机制研究"),
            ("项目负责人", "周清岚"),
            ("所在单位", "教育学院"),
        ],
        body="本人承诺遵守科研诚信及数据管理要求。涉及课程学习数据和问卷数据的研究将在正式采集前完成必要的伦理与数据合规确认。",
        sign_name="周清岚",
        sign_date="2026-10-02",
    )


def build_pkg_c() -> None:
    title = "面向脑机交互信号的轻量级自监督表征学习研究"
    application(
        HERE / "PKG-C" / "C1_项目申报书.pdf",
        code="C1",
        file_label="文件 C1  项目申报书",
        title=title,
        fields=[
            ("项目类别", "自然科学类"),
            ("项目负责人", "顾言澈"),
            ("所在单位", "自动化学院"),
            ("执行期", "2027-01-15 至 2028-10-31"),
            ("申请经费", "28.60万元"),
        ],
        sections=[
            (
                "一、项目摘要",
                "本项目研究低样本条件下脑机交互信号的自监督表征学习方法，拟通过公开数据集与合作单位提供的去标识化历史数据验证模型鲁棒性。项目计划购置高性能便携式脑电采集设备用于算法验证与重复性测试。",
            ),
            (
                "二、数据来源与合规说明",
                "拟使用公开脑电基准数据，以及合作单位提供的去标识化历史脑电数据。现阶段材料未附合作单位的数据授权文件，也未给出学校伦理审批编号；项目组说明正式实验前将补齐相关审批与授权。",
            ),
        ],
    )
    budget(
        HERE / "PKG-C" / "C2_经费预算表.pdf",
        code="C2",
        file_label="文件 C2  经费预算表",
        title=title,
        fields=[("项目负责人", "顾言澈"), ("申请总额", "286000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[
            ["设备费", "98,000", "便携式64导脑电采集设备1套，98000元"],
            ["材料费", "18,000", "电极帽、导电膏及耗材"],
            ["测试加工费", "42,000", "数据采集与设备校准"],
            ["差旅/交流费", "36,000", "合作实验及学术交流"],
            ["出版/文献费", "32,000", "论文与文献"],
            ["劳务费", "60,000", "助研"],
            ["合计", "286,000", "与申请总额一致"],
        ],
        widths=[120, 90, 313],
        note="设备明细：便携式64导脑电采集设备 1 套，单价 98000 元。当前包内未提供设备必要性说明。",
    )
    commitment(
        HERE / "PKG-C" / "C3_科研诚信与合规承诺书.pdf",
        code="C3",
        file_label="文件 C3  科研诚信与合规承诺书",
        fields=[("项目名称", title), ("项目负责人", "顾言澈"), ("所在单位", "自动化学院")],
        body="本人承诺遵守科研诚信、人体研究伦理、个人信息保护与数据安全相关要求。对合作单位数据，将在取得有效授权和完成校内审批后使用。",
        sign_name="顾言澈",
        sign_date="2026-09-26",
        extra="包内附件说明：当前包内未提供“单台/套设备金额达到5万元时的设备必要性说明”。",
    )


def build_case_006() -> None:
    """Budget whose project title is a blurred scan image, not native text."""
    path = VARIANTS / "B2_经费预算表_标题模糊.pdf"
    doc = _new()
    page = _page(doc)
    y = _header(page, "B2-V006", "文件 B2  经费预算表（标题扫描件）")
    page.insert_text((LEFT, y + 10), "项目名称（扫描件，原生文本层无完整标题）", fontname=FONT, fontsize=9)
    y += 18
    title = "生成式人工智能辅助大学生学术写作的认知负荷与反馈机制研究"
    img = Image.new("RGB", (1400, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((8, 28), title, font=_cjk_font(28), fill=(40, 40, 40))
    img = img.filter(ImageFilter.GaussianBlur(radius=3.2))
    _insert_png(page, pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + 48), img)
    y += 56
    y = _kv(page, y, [("项目负责人", "周清岚"), ("申请总额", "153000元")])
    _table(
        page,
        y,
        ["科目", "金额（元）", "说明"],
        [["合计", "153,000", "预算科目合计"]],
        [150, 90, 283],
    )
    _save(doc, path)


def build_case_008() -> None:
    title = "面向低功耗边缘计算的自适应任务调度方法研究"
    budget(
        VARIANTS / "A2_经费预算表_负责人林书彦.pdf",
        code="A2-V008",
        file_label="文件 A2  经费预算表（负责人变体）",
        title=title,
        fields=[("项目负责人", "林书彦"), ("申请总额", "300000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[["合计", "300,000", "与申请总额一致"]],
        widths=[150, 90, 283],
        note="本变体仅将预算表负责人改为“林书彦”；申报书与承诺书仍为林书言。",
    )


def build_case_009() -> None:
    path = VARIANTS / "B3_承诺书_手写负责人.pdf"
    doc = _new()
    page = _page(doc)
    y = _header(page, "B3-V009", "文件 B3  科研诚信与合规承诺书（手写负责人）")
    y = _kv(
        page,
        y,
        [
            ("项目名称", "生成式人工智能辅助大学生学术写作的认知负荷与反馈机制研究"),
            ("所在单位", "教育学院"),
        ],
    )
    page.insert_text((LEFT, y + 12), "项目负责人（手写，原生文本不含可区分姓名）", fontname=FONT, fontsize=9)
    y += 20
    img = Image.new("RGB", (900, 140), "white")
    draw = ImageDraw.Draw(img)
    font = _cjk_font(48)
    draw.text((20, 30), "周清岚", font=font, fill=(20, 20, 20))
    draw.text((28, 38), "周清风", font=font, fill=(90, 90, 90))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    _insert_png(page, pymupdf.Rect(LEFT, y, LEFT + 280, y + 56), img)
    y += 70
    y = _para(page, y, "本人承诺遵守科研诚信及数据管理要求。", height=40)
    _kv(page, y, [("签署日期", "2026-09-20")])
    _save(doc, path)


def build_case_012() -> None:
    application(
        VARIANTS / "A1_项目申报书_周期约两年.pdf",
        code="A1-V012",
        file_label="文件 A1  项目申报书（周期语义不足）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[
            ("项目类别", "自然科学类"),
            ("项目负责人", "林书言"),
            ("所在单位", "信息工程学院"),
            ("执行期", "计划执行约两年，立项后启动"),
            ("申请经费", "30.00万元"),
        ],
        sections=[("一、说明", "本变体不给出确定的开始日与结束日，无法直接核验窗口和精确时长。")],
    )


def build_case_015() -> None:
    application(
        VARIANTS / "A1_项目申报书_类别缺失.pdf",
        code="A1-V015",
        file_label="文件 A1  项目申报书（类别缺失）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[
            ("项目负责人", "林书言"),
            ("所在单位", "信息工程学院"),
            ("执行期", "2027-01-01 至 2028-12-31"),
            ("申请经费", "18.00万元"),
        ],
        sections=[
            ("一、说明", "本变体未填写项目类别。申请经费 180000 元。不同类别上限不同，无法选择阈值。"),
        ],
    )


def build_case_017() -> None:
    budget(
        VARIANTS / "A2_经费预算表_合计差额2元.pdf",
        code="A2-V017",
        file_label="文件 A2  经费预算表（合计差额）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[("项目负责人", "林书言"), ("申请总额", "150000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[
            ["设备费", "40,000", "测试设备"],
            ["材料费", "20,000", "耗材"],
            ["测试化验加工费", "30,000", "测试"],
            ["差旅/会议/交流费", "20,000", "交流"],
            ["出版/文献/信息传播费", "15,000", "文献"],
            ["劳务费", "24,998", "助研"],
            ["科目合计", "149,998", "各科目之和"],
        ],
        widths=[150, 90, 283],
        note="申请总额 150000 元，科目合计 149998 元，差额 2 元，大于 1 元容差。",
    )


def build_case_018() -> None:
    path = VARIANTS / "A2_经费预算表_配套经费口径不清.pdf"
    doc = _new()
    page = _page(doc)
    y = _header(page, "A2-V018", "文件 A2  经费预算表（合并单元格）")
    _textbox(
        page,
        pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + 24),
        "面向低功耗边缘计算的自适应任务调度方法研究",
        size=12,
    )
    y += 28
    y = _kv(page, y, [("项目负责人", "林书言"), ("申请总额", "300000元")])
    y = _section(page, y, "一、科目预算")
    y = _table(
        page,
        y,
        ["科目", "金额（元）", "说明"],
        [
            ["设备费", "48,000", "开发板与模块"],
            ["材料费", "22,000", "耗材"],
            ["测试化验加工费", "60,000", "测试"],
            ["差旅/会议/交流费", "45,000", "交流"],
            ["出版/文献/信息传播费", "35,000", "文献"],
            ["劳务费", "90,000", "助研"],
        ],
        [150, 90, 283],
    )
    merged = pymupdf.Rect(LEFT, y, PAGE.width - RIGHT, y + 36)
    page.draw_rect(merged, fill=(0.98, 0.96, 0.90), color=(0.7, 0.73, 0.78), width=0.4)
    _textbox(
        page,
        pymupdf.Rect(merged.x0 + 6, merged.y0 + 6, merged.x1 - 6, merged.y1 - 4),
        "其中：配套经费 20,000 元（合并单元格，未标明是否计入申请总额）",
        size=10,
    )
    y = merged.y1 + 10
    _para(
        page,
        y,
        "表格存在“其中：配套经费”合并单元格，无法判断该 20000 元是否应计入申请总额或科目合计。",
        height=48,
    )
    _save(doc, path)


def build_case_021() -> None:
    application(
        VARIANTS / "A1_项目申报书_总经费与申请经费.pdf",
        code="A1-V021",
        file_label="文件 A1  项目申报书（经费语义并存）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[
            ("项目类别", "自然科学类"),
            ("项目负责人", "林书言"),
            ("项目总经费", "20万元"),
            ("申请经费", "15万元"),
        ],
        sections=[
            (
                "一、说明",
                "申报书同时出现“项目总经费 20 万元”和“申请经费 15 万元”。请与同目录 A2_经费预算表_申请总额20万元.pdf 对照：预算申请总额为 200000 元，不能确认应比较总经费还是申请经费。",
            ),
        ],
    )
    budget(
        VARIANTS / "A2_经费预算表_申请总额20万元.pdf",
        code="A2-V021",
        file_label="文件 A2  经费预算表（与总经费/申请经费变体配套）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[("项目负责人", "林书言"), ("申请总额", "200000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[["合计", "200,000", "与申请总额一致"]],
        widths=[150, 90, 283],
        note="本表申请总额 200000 元。与申报书中的项目总经费 20 万元相同、与申请经费 15 万元不同。",
    )


def build_case_024() -> None:
    budget(
        VARIANTS / "C2_经费预算表_设备无单价.pdf",
        code="C2-V024",
        file_label="文件 C2  经费预算表（设备无单价）",
        title="面向脑机交互信号的轻量级自监督表征学习研究",
        fields=[("项目负责人", "顾言澈"), ("申请总额", "286000元")],
        headers=["科目", "金额（元）", "说明"],
        rows=[
            ["设备费", "90,000", "设备2套共90000元，无单价或型号拆分"],
            ["材料费", "18,000", "耗材"],
            ["测试加工费", "42,000", "测试"],
            ["差旅/交流费", "36,000", "交流"],
            ["出版/文献费", "32,000", "文献"],
            ["劳务费", "68,000", "助研"],
            ["合计", "286,000", "与申请总额一致"],
        ],
        widths=[120, 90, 313],
        note="设备仅写“设备2套共90000元”，无单台/套单价，无法判断是否存在单台/套≥50000元。",
    )


def build_case_027() -> None:
    application(
        VARIANTS / "A1_项目申报书_招募受试者.pdf",
        code="A1-V027",
        file_label="文件 A1  项目申报书（明确招募受试者）",
        title="面向低功耗边缘计算的自适应任务调度方法研究",
        fields=[
            ("项目类别", "自然科学类"),
            ("项目负责人", "林书言"),
            ("执行期", "2027-01-01 至 2028-12-31"),
            ("申请经费", "30.00万元"),
        ],
        sections=[
            (
                "一、人体受试者",
                "本项目明确招募20名受试者，采集操作负荷与疲劳主观评分。政策要求提交时提供伦理审批编号或审批进行中证明。当前包已冻结，既无审批编号也无进行中证明。",
            ),
        ],
    )


def build_case_030() -> None:
    path = VARIANTS / "B3_承诺书_签署日期遮挡.pdf"
    doc = _new()
    page = _page(doc)
    y = _header(page, "B3-V030", "文件 B3  科研诚信与合规承诺书（日期遮挡）")
    y = _kv(
        page,
        y,
        [
            ("项目名称", "生成式人工智能辅助大学生学术写作的认知负荷与反馈机制研究"),
            ("项目负责人", "周清岚"),
        ],
    )
    y = _para(page, y, "本人承诺遵守科研诚信及数据管理要求。", height=40)
    y = _section(page, y, "二、签署")
    y = _kv(page, y, [("项目负责人", "周清岚")])
    page.insert_text((LEFT, y + 14), "签署日期", fontname=FONT, fontsize=10)
    page.insert_text((LEFT + 92, y + 14), "2026-09-2", fontname="helv", fontsize=12)
    cx, cy, r = LEFT + 168, y + 10, 28
    page.draw_circle(pymupdf.Point(cx, cy), r, color=(0.75, 0.12, 0.12), width=2)
    page.insert_text((cx - 22, cy + 4), "学院章", fontname=FONT, fontsize=9, color=(0.75, 0.12, 0.12))
    page.insert_text((LEFT, y + 48), "印章遮挡后仅能识别“2026-09-2?”。", fontname=FONT, fontsize=9)
    _save(doc, path)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    VARIANTS.mkdir(parents=True, exist_ok=True)
    build_pkg_b()
    build_pkg_c()
    build_case_006()
    build_case_008()
    build_case_009()
    build_case_012()
    build_case_015()
    build_case_017()
    build_case_018()
    build_case_021()
    build_case_024()
    build_case_027()
    build_case_030()
    print(f"wrote PDFs under {HERE} and copied names into {FIXTURES}")


if __name__ == "__main__":
    main()
