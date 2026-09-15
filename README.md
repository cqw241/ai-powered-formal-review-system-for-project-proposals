# 规证AI

高校项目申报材料形式审查辅助应用。当前分支实现 **B01 应用基础**：可运行前后端、项目持久化，以及可复用的服务端云端图像调用模块。

> 本版本仅供本地开发。未实现登录与项目权限，**不要当作可安全公开部署的版本**。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + TypeScript + Vite（npm） |
| 后端 | FastAPI + SQLAlchemy |
| 数据库 | SQLite（文件持久化） |
| Python 环境 | 项目本地 `uv`（锁文件 `backend/uv.lock`） |
| 模型接入 | OpenAI 兼容云端多模态 API（默认按 DashScope compatible-mode 验证） |

## 目录结构

```text
backend/
  app/                 # FastAPI 应用
    api/projects.py    # 项目列表 / 创建 / 详情
    llm/               # 云端图像调用适配器（供 B02/B03 复用）
    db.py              # SQLite 初始化（create_all，不清空数据）
  fixtures/            # 无敏感测试图像
  scripts/smoke_vision.py
  tests/
frontend/              # Web UI
docs/competition-review/B01_实现与验收记录.md
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

**未填写模型密钥时，项目创建/查询仍可使用。** 模型相关变量：

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 默认 `sqlite:///./backend/data/app.db`；普通启动不会清空 |
| `CORS_ORIGINS` | 前端开发源 |
| `LLM_BASE_URL` | OpenAI 兼容根地址，例如 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL` | 支持图像输入的模型 ID，例如 `qwen3.8-27b` 或 `qwen3-vl-plus` |
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

首次启动会在 `backend/data/app.db` 创建表；**重启不会删除已有项目**。

### 数据库初始化

应用启动时自动执行 `create_all`（只建缺失表）。也可手动：

```bash
cd backend
uv run python -c "from app.db import init_db; init_db(); print('ok')"
```

## API（B01）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查；`llm_configured` 仅表示配置是否齐全 |
| `GET` | `/api/projects` | 项目列表（按创建时间倒序） |
| `POST` | `/api/projects` | 创建项目；body: `{ "name": "..." }` |
| `GET` | `/api/projects/{id}` | 项目详情；不存在返回 404 |

项目字段：`id`、`name`、`created_at`。界面中未审查项目显示为「尚未审查」，不会显示「审查通过」。

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

默认读取 `backend/fixtures/vision_smoke.png`。提示词不直接给出图中答案；脚本会检查结果是否包含图像内容证据。

## 测试与检查

```bash
# 后端
cd backend
uv run pytest -q

# 前端类型检查 + 构建
cd frontend
npm run build
```

## B02 / B03 接续入口

- 项目实体与 API：`backend/app/api/projects.py`、`backend/app/models.py`
- 图像理解：`app.llm.analyze_image(ImageInput(...), prompt=...)`
- 前端列表/详情：`frontend/src/App.tsx`、`frontend/src/api.ts`

B02 再做 PDF 上传与页图；B03 再做申请经费提取与问题卡。

## 验收记录

见 [docs/competition-review/B01_实现与验收记录.md](docs/competition-review/B01_实现与验收记录.md)。

## 可选：安装环境排障

仅当本机文件系统无法创建符号链接、导致 `uv sync` / `npm install` 失败时才需要。**不是产品要求，也不是默认安装步骤。**

- 后端：将虚拟环境放到支持 symlink 的目录，例如  
  `UV_PROJECT_ENVIRONMENT="$HOME/.venvs/guizheng-ai-backend" UV_LINK_MODE=copy uv sync --extra dev`
- 前端：`npm install --no-bin-links`；必要时用  
  `node node_modules/vite/bin/vite.js` 代替 `npm run dev`
