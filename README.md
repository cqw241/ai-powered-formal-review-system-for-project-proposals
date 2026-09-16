# 规证AI

高校项目申报材料形式审查辅助应用。当前分支实现 **B04 政策导入**：在 B01–B03 项目持久化、PDF 上传预览与申请经费核对之上，支持政策 PDF 上传、原文预览与候选要求提取（可编辑草稿）。

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
    services/money.py
    services/funding_extract.py
    services/funding_review.py
    services/policy_extract.py
    services/policy_storage.py
    services/pdf.py
    llm/                # 云端图像调用（文本不足时可选回退）
  fixtures/pdfs/        # A1/A2/A3、A2_320000、POL 申报指南
  tests/
frontend/
  src/PolicyWorkspace.tsx
docs/competition-review/
  B01_实现与验收记录.md
  B02_实现与验收记录.md
  B03_实现与验收记录.md
  B04_实现与验收记录.md
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

首次启动会在 `backend/data/app.db` 创建表，并在 `MATERIALS_DIR` / `POLICIES_DIR` 保存上传文件；**重启不会删除已有项目、材料、经费核对结果与政策候选**。

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
| `POST` | `/api/projects/{project_id}/funding-review` | 提取并比较申请经费，持久化问题卡 |
| `GET` | `/api/projects/{project_id}/funding-review` | 最新核对结果；无则 404 |

比较对象仅为「申请经费 / 申请总额」。单位换算：万元×10000、千元×1000、元保持不变。未知值不当作 0。

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

## B04 使用说明

1. 顶部导航进入「政策与候选要求」。
2. 上传模拟申报指南 PDF（见下）。
3. 在原文预览中翻页查看页图与原生文本。
4. 查看候选列表；P-05 应出现自然科学类 ≤30 万元、人文社会科学类 ≤15 万元，并带来源。
5. 点击候选可修改类别/金额/摘录等，保存后刷新仍保留。

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

## B05 接续入口

- 政策详情与候选草稿：`/api/policies/{id}`
- 候选编辑持久化：`PATCH /api/policies/{id}/candidates/{candidate_id}`
- 经费上限草稿字段：`kind=FUNDING_CAP` + `category` + `amount_yuan`（可映射 RULE-005）
- 前端政策工作台：`frontend/src/PolicyWorkspace.tsx`

B05 再做规则编辑、启用/停用与版本记录；**不要**在本分支提前实现。

## 验收记录

- [B01 实现与验收记录](docs/competition-review/B01_实现与验收记录.md)
- [B02 实现与验收记录](docs/competition-review/B02_实现与验收记录.md)
- [B03 实现与验收记录](docs/competition-review/B03_实现与验收记录.md)
- [B04 实现与验收记录](docs/competition-review/B04_实现与验收记录.md)

## 可选：安装环境排障

仅当本机文件系统无法创建符号链接、导致 `uv sync` / `npm install` 失败时才需要。**不是产品要求，也不是默认安装步骤。**

- 后端：将虚拟环境放到支持 symlink 的目录，例如  
  `UV_PROJECT_ENVIRONMENT="$HOME/.venvs/guizheng-ai-backend" UV_LINK_MODE=copy uv sync --extra dev`
- 前端：`npm install --no-bin-links`；必要时用  
  `node node_modules/vite/bin/vite.js` 代替 `npm run dev`
