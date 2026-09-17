# 规证AI

高校项目申报材料形式审查辅助应用。当前分支做 **W4**：B10 材料要求（RULE-001/008/009）与 B11 证据对照（通用问题详情、双文档对照、扫描页高亮）；一次审查运行 RULE-001/002/003/004/005/006/007/008/009/010。

> 本版本仅供本地开发。未实现登录与项目权限，**不要当作可安全公开部署的版本**。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + TypeScript + Vite（npm） |
| 后端 | FastAPI + SQLAlchemy |
| 数据库 | SQLite（文件持久化） |
| PDF | PyMuPDF（页文本、页图、金额定位） |
| Python 环境 | 项目本地 `uv`（锁文件 `backend/uv.lock`） |
| 模型接入 | OpenAI 兼容云端多模态 API（可选；数字 PDF 文本提取不依赖） |

## 目录结构

```text
backend/
  app/
    api/projects.py     # 项目列表 / 创建 / 详情
    api/materials.py    # 材料上传 / 列表 / 页文本 / 页图
    api/funding.py      # 申请经费核对
    api/policies.py     # 政策上传 / 预览 / 候选要求
    api/rules.py        # 规则启用 / 停用 / 版本编辑
    api/reviews.py      # 审查任务
    services/money.py
    services/funding_extract.py
    services/funding_review.py
    services/identity_extract.py
    services/identity_review.py
    services/policy_extract.py
    services/policy_storage.py
    services/rules.py
    services/reviews.py
    services/evidence_compare.py
    services/budget_extract.py
    services/budget_review.py
    services/date_extract.py
    services/date_review.py
    services/material_extract.py
    services/material_review.py
    services/pdf.py
    review_contract.py      # W3 执行器契约
    llm/                # 云端图像调用（文本不足时可选回退）
  fixtures/pdfs/        # A1/A2/A3、A2_320000、POL 申报指南
  tests/
frontend/
  src/PolicyWorkspace.tsx
  src/ReviewWorkspace.tsx
  src/EvidenceCompare.tsx
  src/PagePreview.tsx
docs/competition-review/
  B01_实现与验收记录.md
  B02_实现与验收记录.md
  B03_实现与验收记录.md
  B04_实现与验收记录.md
  B05_实现与验收记录.md
  B06_实现与验收记录.md
  B07_实现与验收记录.md
  B08_实现与验收记录.md
  W3_并行开发契约.md
  W3_接线与验收记录.md
.env.example
```

## 环境准备

### 1. 后端（uv）

```bash
cd backend
uv sync --extra dev
```

### 2. 前端

```bash
cd frontend
npm install
```

### 3. 配置

```bash
cp .env.example .env
```

**未填写模型密钥时，项目、材料/政策上传预览、基于原生文本的经费核对与候选提取仍可使用。** 相关变量：

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 默认 `sqlite:///./backend/data/app.db`；普通启动不会清空 |
| `MATERIALS_DIR` | 上传材料 PDF 存储目录，默认 `./backend/data/materials` |
| `POLICIES_DIR` | 上传政策 PDF 存储目录，默认 `./backend/data/policies` |
| `UPLOAD_MAX_BYTES` | 单文件上传上限，默认 20MB |
| `CORS_ORIGINS` | 前端开发源 |
| `LLM_BASE_URL` | OpenAI 兼容根地址（可选回退） |
| `LLM_MODEL` | 支持图像输入的模型 ID |
| `LLM_API_KEY` | **仅服务端**；勿写入 `VITE_*` |
| `LLM_TIMEOUT_SECONDS` | 请求超时（秒） |

API Key 只放在服务端 `.env`。不要提交真实密钥。

## 启动

在两个终端中分别启动：

```bash
# 终端 1 — 后端
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
# 终端 2 — 前端
cd frontend
npm run dev
```

浏览器打开 Vite 提示的地址（默认 `http://127.0.0.1:5173`）。前端通过 Vite 代理访问 `/api`。

首次启动会在 `backend/data/app.db` 创建表（`create_all` 只补缺失表），并在 `MATERIALS_DIR` / `POLICIES_DIR` 保存上传文件；**重启不会删除已有项目、材料、经费核对结果、政策候选、规则版本与审查任务**。

### 数据库初始化

应用启动时自动执行 `create_all`（只建缺失表）。也可手动：

```bash
cd backend
uv run python -c "from app.db import init_db; init_db(); print('ok')"
```

## API

### 项目（B01）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查；`llm_configured` 仅表示配置是否齐全 |
| `GET` | `/api/projects` | 项目列表（按创建时间倒序） |
| `POST` | `/api/projects` | 创建项目；body: `{ "name": "..." }` |
| `GET` | `/api/projects/{id}` | 项目详情；不存在返回 404 |

### 材料（B02）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/projects/{project_id}/materials` | multipart：`file` + `category` |
| `GET` | `/api/projects/{project_id}/materials` | 材料列表 |
| `GET` | `/api/materials/{material_id}/pages/{page}/text` | 页原生文本（页码从 1 开始） |
| `GET` | `/api/materials/{material_id}/pages/{page}/image` | 页 PNG（页码从 1 开始） |

类别：`APPLICATION` / `BUDGET` / `COMMITMENT` / `OTHER`  
状态：`PROCESSING` / `READY` / `FAILED`

### 申请经费核对（B03）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/projects/{project_id}/funding-review` | 提取并比较申请经费，持久化问题卡；快照当时已启用规则 |
| `GET` | `/api/projects/{project_id}/funding-review` | 最新核对结果；无则 404 |
| `GET` | `/api/projects/{project_id}/funding-reviews` | 历史核对（新到旧）；每条含当时 `bound_rules` |
| `GET` | `/api/projects/{project_id}/funding-reviews/{review_id}` | 指定一次核对及其绑定快照 |

比较对象仅为「申请经费 / 申请总额」。单位换算：万元×10000、千元×1000、元保持不变。未知值不当作 0。RULE-007 行为不变。若启用了 RULE-005，本次核对把当时版本写入 `bound_rules` 并展示类别/上限，可用已提取申请经费对照；**这不是按上限判 PASS/FAIL**（留给 B08），缺类别也不判 FAIL。

### 政策与候选要求（B04）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/policies` | multipart：`file`；上传政策 PDF 并提取候选 |
| `GET` | `/api/policies` | 政策列表 |
| `GET` | `/api/policies/{id}` | 政策详情与候选列表 |
| `GET` | `/api/policies/{id}/pages/{page}/text` | 政策页文本 |
| `GET` | `/api/policies/{id}/pages/{page}/image` | 政策页 PNG |
| `PATCH` | `/api/policies/{id}/candidates/{candidate_id}` | 编辑候选草稿并持久化 |

候选是可编辑草稿，**不是**已启用规则。经费上限类保留类别、金额（含单位/规范化元）与来源（条款/页码/摘录）。

### 规则启用与版本（B05）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/policies/{id}/candidates/{candidate_id}/enable` | 从 FUNDING_CAP 草稿生成 Rule + RuleVersion（`rule_code=RULE-005`）；已有则重新启用 |
| `GET` | `/api/rules` | 规则列表（含版本） |
| `GET` | `/api/rules/{id}` | 规则详情 |
| `PATCH` | `/api/rules/{id}` | 编辑已启用规则：新增版本，不改历史版本 |
| `POST` | `/api/rules/{id}/disable` | 停用（只改 `enabled`，不删版本） |
| `POST` | `/api/rules/{id}/enable` | 重新启用（不新增版本） |

规则至少保留：名称、政策出处、类别、比较对象（申请经费）、要求（comparator + amount_yuan，未知不当 0）。

启用后改上限必须走 `PATCH /api/rules/{id}`（「编辑已启用规则」，追加版本）。只改候选草稿，新审查不会用到。自然科学类与人文社会科学类会各生成一条 `rule_code=RULE-005` 的规则（B05 允许；B08 按类别选用对应那一条）。

### 审查工作台（B06）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/projects/{project_id}/reviews` | 发起审查任务；body: `{ "rule_ids": ["..."] }` |
| `GET` | `/api/projects/{project_id}/reviews` | 任务列表（新到旧，含逐项结果） |
| `GET` | `/api/projects/{project_id}/reviews/{task_id}` | 指定任务 |

条目 = 内置 RULE-001/002/003/004/006/007/008/009/010 + 勾选的已启用规则（通常是 RULE-005）。发起时先落库「运行中」任务与逐项占位，刷新可续看；结束后再写终态并写入 `review_item_results`。RULE-007 仍复用经费核对。PASS/FAIL/NOT_APPLICABLE → 已完成；NEED_HUMAN_REVIEW → 待确认；SYSTEM_ERROR → 失败。规则 FAIL ≠ 任务失败。现有「开始核对」入口保留。POST 需要 JSON body。

## B06 / W3 使用说明

1. 顶部导航进入「政策与候选要求」，上传模拟申报指南 PDF，将对应类别的经费上限启为规则。
2. 打开项目，上传申报书、预算表和承诺书。
3. 在「审查工作台」确认内置规则已锁定，勾选 RULE-005，点击「开始审查」。
4. 同一任务中查看必需材料、名称、负责人、周期、上限、预算合计、申请经费一致、设备附件、伦理适用性和签署日期的逐项结果；点击「对照原文」查看字段名、原值、单位与差异，并可打开对应页。扫描页高亮随缩放对齐；申请经费与总经费分别标明。
5. 刷新后再打开该项目，任务与逐项状态仍在。
6. 下方「开始核对」仍可单独跑 RULE-007。

样例政策：

- `docs/kickoff/development_batch/policy/2026青年科研创新培育计划申报指南_开发批次规范.pdf`
- `backend/fixtures/pdfs/POL_申报指南_开发批次规范.pdf`（同上副本，便于测试）
- Markdown 源（开发对照）：`docs/kickoff/development_batch/policy/2026青年科研创新培育计划_申报指南.md`

## 模型调用模块

入口：`backend/app/llm/`。B03/B04 数字 PDF 默认走原生文本；配置了 `LLM_*` 时，材料经费提取在文本不足时可尝试页图视觉回退。密钥仅在服务端。

```bash
cd backend
uv run python scripts/smoke_vision.py
```

## 测试与检查

```bash
# 后端
cd backend
uv run pytest -q

# 前端类型检查 + 构建
cd frontend
npm run build
```

## W3 / W4 一次审查（本分支）

开始审查后，同一任务内置并执行：

| 规则 | 执行器 | 行为 |
|---|---|---|
| RULE-001 | `execute_material_rule` | 申报书、预算表、承诺书齐全且可读；上传未完成不判缺件 |
| RULE-002 | `execute_identity_rule` | 三类材料项目名称，Unicode 空白归一后比较 |
| RULE-003 | `execute_identity_rule` | 三类材料负责人姓名 |
| RULE-004 | `execute_date_rule` | 执行期窗口与 ≤24 个月 |
| RULE-005 | `execute_budget_rule` | 分类经费上限（勾选已启用规则；按项目类别选用对应快照） |
| RULE-006 | `execute_budget_rule` | 预算科目合计，容差 1 元 |
| RULE-007 | 既有经费核对 | 申报书申请经费 vs 预算申请总额 |
| RULE-008 | `execute_material_rule` | 单台/套 ≥5 万元须有设备必要性说明；否则不适用 |
| RULE-009 | `execute_material_rule` | 数据/伦理适用性；不清时列出依据与待确认问题 |
| RULE-010 | `execute_date_rule` | 承诺书签署日期 |

结果写入 `review_item_results`，工作台展示摘要、双文档对照与可点击原文证据。PASS/FAIL/NOT_APPLICABLE → 已完成；NEED_HUMAN_REVIEW → 待确认；SYSTEM_ERROR → 失败。规则 FAIL ≠ 任务失败。无已启用 RULE-005 时仍跑其余内置规则。B12 人工处置不在本分支。

## B12 接续入口

- 审查任务：`POST/GET /api/projects/{id}/reviews`
- 通用结果：`review_item_results` + `RuleExecutionResult.evidence`
- 已启用规则：`GET /api/rules`

B12 再做确认问题、修正字段、标记不适用（依赖 B10/B11）。**不要**在本分支提前实现。

## 验收记录

- [B01 实现与验收记录](docs/competition-review/B01_实现与验收记录.md)
- [B02 实现与验收记录](docs/competition-review/B02_实现与验收记录.md)
- [B03 实现与验收记录](docs/competition-review/B03_实现与验收记录.md)
- [B04 实现与验收记录](docs/competition-review/B04_实现与验收记录.md)
- [B05 实现与验收记录](docs/competition-review/B05_实现与验收记录.md)
- [B06 实现与验收记录](docs/competition-review/B06_实现与验收记录.md)
- [B07 实现与验收记录](docs/competition-review/B07_实现与验收记录.md)
- [B08 实现与验收记录](docs/competition-review/B08_实现与验收记录.md)
- [W3 接线与验收记录](docs/competition-review/W3_接线与验收记录.md)
- [B10 实现与验收记录](docs/competition-review/B10_实现与验收记录.md)

## 可选：安装环境排障

仅当本机文件系统无法创建符号链接、导致 `uv sync` / `npm install` 失败时才需要。**不是产品要求，也不是默认安装步骤。**

- 后端：将虚拟环境放到支持 symlink 的目录，例如  
  `UV_PROJECT_ENVIRONMENT="$HOME/.venvs/guizheng-ai-backend" UV_LINK_MODE=copy uv sync --extra dev`
- 前端：`npm install --no-bin-links`；必要时用  
  `node node_modules/vite/bin/vite.js` 代替 `npm run dev`
