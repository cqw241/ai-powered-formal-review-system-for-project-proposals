# 规证AI

高校项目申报材料形式审查辅助应用。当前分支实现 **B02 PDF 上传与预览**：在 B01 项目持久化与云端图像适配器之上，支持多份 PDF 上传、材料清单、页图翻页预览与原生文本读取。

> 本版本仅供本地开发。未实现登录与项目权限，**不要当作可安全公开部署的版本**。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + TypeScript + Vite（npm） |
| 后端 | FastAPI + SQLAlchemy |
| 数据库 | SQLite（文件持久化） |
| PDF | PyMuPDF（同步解析页数、原生文本与页图） |
| Python 环境 | 项目本地 `uv`（锁文件 `backend/uv.lock`） |
| 模型接入 | OpenAI 兼容云端多模态 API（默认按 DashScope compatible-mode 验证） |

## 目录结构

```text
backend/
  app/                 # FastAPI 应用
    api/projects.py    # 项目列表 / 创建 / 详情
    api/materials.py   # 材料上传 / 列表 / 页文本 / 页图
    services/pdf.py    # PyMuPDF 页读取
    services/storage.py
    llm/               # 云端图像调用适配器（供 B03 复用）
    db.py              # SQLite 初始化（create_all，不清空数据）
  data/                # 运行时：app.db、materials/（gitignore）
  fixtures/
    pdfs/              # B02 测试样例 PDF（A1/A2/A3）
    vision_smoke.png
  scripts/smoke_vision.py
  tests/
frontend/              # Web UI（项目详情含上传 / 列表 / 预览）
docs/competition-review/
  B01_实现与验收记录.md
  B02_实现与验收记录.md
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

**未填写模型密钥时，项目与材料上传/预览仍可使用。** 相关变量：

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 默认 `sqlite:///./backend/data/app.db`；普通启动不会清空 |
| `MATERIALS_DIR` | 上传 PDF 存储目录，默认 `./backend/data/materials` |
| `UPLOAD_MAX_BYTES` | 单文件上传上限，默认 20MB |
| `CORS_ORIGINS` | 前端开发源 |
| `LLM_BASE_URL` | OpenAI 兼容根地址 |
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

首次启动会在 `backend/data/app.db` 创建表，并在 `MATERIALS_DIR` 保存上传文件；**重启不会删除已有项目与材料**。

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

项目字段：`id`、`name`、`created_at`。界面中未审查项目显示为「尚未审查」，不会显示「审查通过」。

### 材料（B02）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/projects/{project_id}/materials` | multipart：`file` + `category`；同步解析后返回材料记录 |
| `GET` | `/api/projects/{project_id}/materials` | 项目材料列表（按创建时间倒序） |
| `GET` | `/api/materials/{material_id}/pages/{page}/text` | 指定页原生文本（页码从 **1** 开始） |
| `GET` | `/api/materials/{material_id}/pages/{page}/image` | 指定页 PNG 图像（页码从 **1** 开始） |

材料字段：`id`、`project_id`、`original_filename`、`category`、`page_count`、`status`、`error_summary`、`created_at`。

- 类别：`APPLICATION` / `BUDGET` / `COMMITMENT` / `OTHER`
- 状态：`PROCESSING` / `READY` / `FAILED`（本阶段同步处理，成功响应多为 `READY` 或 `FAILED`）
- 服务端以材料 ID 保存文件；拒绝非 PDF、空文件与超大文件；损坏 PDF 记为 `FAILED` 并给出原因
- 越界页码、材料不存在、文件丢失返回明确错误

## B02 使用说明

1. 打开应用，创建或进入已有项目。
2. 在「材料上传」选择类别（如申报书 / 预算表），选择一个或多个 PDF，点击「上传材料」。
3. 「材料列表」显示类别、文件名、页数与状态；解析失败会显示具体原因。
4. 点击 **就绪** 材料，右侧/下方预览区显示页图；用「上一页 / 下一页」翻页，并可查看本页原生文本。
5. 刷新页面或重启后端后，材料记录与预览仍可用。

测试样例 PDF：`backend/fixtures/pdfs/`（`A1_项目申报书.pdf`、`A2_经费预算表.pdf`、`A3_科研诚信与合规承诺书.pdf`）。

## 模型调用模块

入口：`backend/app/llm/`。

- 从服务端配置读取 `LLM_*`
- 发送图像（`image_url` data URL）+ 文本提示
- 优先使用供应商 `response_format.json_schema`
- 再用 Pydantic **本地严格校验**，转为应用自有 `ImageAnalysisResult`
- 配置缺失、超时、鉴权失败、上游错误、结构无效时抛出明确错误；**不静默 mock、不伪装成功**

开发验证（CLI，非产品页、非公开调试接口）：

```bash
cd backend
# 先在仓库根目录 .env 配好 LLM_*
uv run python scripts/smoke_vision.py
```

默认读取 `backend/fixtures/vision_smoke.png`。本轮未改模型适配器时，可不重复跑付费云端 smoke。

## 测试与检查

```bash
# 后端
cd backend
uv run pytest -q

# 前端类型检查 + 构建
cd frontend
npm run build
```

## B03 接续入口

- 项目实体与 API：`backend/app/api/projects.py`、`backend/app/models.py`
- 材料与页读取：`backend/app/api/materials.py`、`app.services.pdf`
- 图像理解：`app.llm.analyze_image(ImageInput(...), prompt=...)`
- 前端列表/详情/预览：`frontend/src/App.tsx`、`frontend/src/api.ts`

B03 再做申请经费提取与问题卡。

## 验收记录

- [B01 实现与验收记录](docs/competition-review/B01_实现与验收记录.md)
- [B02 实现与验收记录](docs/competition-review/B02_实现与验收记录.md)

## 可选：安装环境排障

仅当本机文件系统无法创建符号链接、导致 `uv sync` / `npm install` 失败时才需要。**不是产品要求，也不是默认安装步骤。**

- 后端：将虚拟环境放到支持 symlink 的目录，例如  
  `UV_PROJECT_ENVIRONMENT="$HOME/.venvs/guizheng-ai-backend" UV_LINK_MODE=copy uv sync --extra dev`
- 前端：`npm install --no-bin-links`；必要时用  
  `node node_modules/vite/bin/vite.js` 代替 `npm run dev`
