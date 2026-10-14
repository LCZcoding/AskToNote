# AskToNote

提问式学习智能体。从一个问题出发，AI 边回答、边让你划词标注陌生点，并把每次解释**原地扩写进同一份 Markdown 笔记**——最终既搞懂一串知识，也留下一份结构化笔记。

> 核心主张：**每一个陌生点，都是一个可以延伸的问题；每一次标注，都是笔记的扩写。**

设计文档（SDD 三层）：[Spec.md](docs/Spec.md) · [Plan.md](docs/Plan.md) · [Tasks.md](docs/Tasks.md)

## 核心特性

- **回答即笔记**：AI 流式（SSE）回答直接追加进笔记区，以 `## 问题` + 回答正文的 Q/A 区块落盘，无独立「AI 回答区」。
- **手动划词标注**：选区 ≤ 60 字 → 标注，AI 生成候选问题待确认；> 60 字 → 自动降级为「追问」，作为引用片段走提问链路。
- **原地扩写**：标注可选「简单了解」（插入括号简释）或「深入了解」（追加 `####` 章节到笔记末尾 + 双向可点击跳转）。
- **用户知识记忆**：概念掌握度只由用户显式行为驱动——标注（不改分）与「已理解」（+10，幂等）；背景由概念记忆自动推断，可手动修改并锁定。
- **主题管理**：一个主题一个 `data/notes/{slug}.md`；侧边栏切换 / 显式新建，跨主题用 `[[slug#概念]]` 正向内链。
- **一键导出**：纯前端把当前笔记 Markdown 原文导出为 `.md`。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13 + FastAPI + Uvicorn（`uv` 管理），SQLite（标准库 `sqlite3`） |
| LLM | DeepSeek，OpenAI 兼容 SDK，模型可配置（默认 `deepseek-flash`） |
| 前端 | Vite + React + TypeScript + zustand + react-markdown |

## 目录结构

```
AskToNote/
├── docs/            # Spec.md / Plan.md / Tasks.md（SDD 文档）
├── backend/
│   ├── main.py      # FastAPI 入口（挂载路由 + CORS）
│   ├── app/         # config / llm / prompts / parse / memory / notes / api
│   │   └── agents/  # answer_note / question_optimizer / background
│   ├── tests/       # pytest 单元测试
│   ├── data/        # asktonote.db + notes/*.md
│   └── .env.example
└── frontend/
    └── src/         # store / api / types / 组件（NoteView、AskBar…）
```

## 快速开始

### 1. 后端

```bash
cd backend

# 配置环境变量
cp .env.example .env      # 编辑 .env，填入 DEEPSEEK_API_KEY

# 安装依赖（uv 会自动创建 .venv）
uv sync

# 启动服务（默认 http://127.0.0.1:8000）
uv run uvicorn main:app --reload
```

`.env` 配置项：

```
DEEPSEEK_API_KEY=your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL=deepseek-flash
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev               # 默认 http://localhost:5173
```

前端 dev 通过 Vite proxy 将 `/api` 转发到 `127.0.0.1:8000`（见 [vite.config.ts](frontend/vite.config.ts)），无需额外配置跨域。

### 3. 测试

```bash
cd backend
uv run pytest
```

## API 概览

| 方法/路径 | 说明 |
|---|---|
| `POST /api/optimize/initial` | 模式A：优化初始问题；`new_topic=true` 时另命名主题 `slug`/`title` |
| `POST /api/optimize/annotation` | 模式B：标注片段 → 候选问题列表 |
| `POST /api/answer` | 模式①：**SSE 流式**返回回答纯 Markdown，落盘为 Q/A 区块 |
| `POST /api/annotate` | 模式②：处理单个标注（含深度），原地扩写笔记 |
| `GET /api/note/{slug}` | 获取指定主题笔记内容 |
| `GET /api/notes` | 主题列表（按 `updated_at` 倒序） |
| `GET /api/memory` | 读用户知识记忆（背景 + 概念掌握度） |
| `POST /api/memory` | 手动纠正用户背景（概念分数只读、不可改） |
| `POST /api/memory/confirm` | 对已标注概念确认「已理解」（清 pending、+10，幂等） |
| `POST /api/memory/refresh` | 会话级刷新背景推断 |

完整契约见 [Plan.md §4](docs/Plan.md)。

## 使用流程

```
提问 → 模式A 优化问题并确认 → 流式回答流入笔记
  → 划词标注陌生点 → 模式B 生成候选问题 → 确认
  → 【一次一个】选「简单/深入了解」→ 原地扩写
  → 读完点「已理解」（记忆 +10）或「跳过」→ 下一条
  → 可继续在新内容上标注（下钻插队优先）→ 循环
```

## 说明

- 单用户 v1；多用户、知识图谱、反向内链、掌握度衰减留待 v2。
- 已知边界与取舍见 [Plan.md §12](docs/Plan.md)。
