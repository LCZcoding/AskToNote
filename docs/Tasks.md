# Tasks.md — 提问式学习智能体（AskToNote）

> 状态：待执行
> SDD 第三步——可执行任务清单，依据 [Plan.md](./Plan.md)（尤其 §10 里程碑）。
> 规则：勾选式（`- [ ]`/`- [x]`）；**严格串行 M1→M5**，一个任务自测通过后再开下一个；**不实现 Spec.md 未定义的功能**，技术细节一律回到 Plan。

## 0. 通用约定

- 每个任务 = 一个可验证产出；完成后勾选，并在任务下补一行实测结果。
- 命令：后端在 `backend/` 下用 `uv run ...`；前端在 `frontend/` 下用 `npm run dev`。
- 所有 LLM 调用只走 `app/llm.py`；模型名从配置读，不写死在业务代码。
- 表结构/字段严格按 Plan §3，接口契约严格按 Plan §4，Prompt 严格按 Plan §6；**契约有偏差先改 Plan 再改代码**。
- 演示阶段不做多用户、不做事务回滚、不做自动化前端测试（见 Plan §12）。

---

## M1 后端核心

**验证标准（Plan §10）**：curl 能流式看到回答，并产出 `data/notes/{slug}.md`；`pytest` 通过。
> 说明：**读接口**（T1.9）与**模式A 基础版**（T1.10）为 M2 起的前置依赖，在此一并落地（Plan §10 M1 已列）→ **M2 不含任何后端工作**。

- [x] **T1.1 依赖与配置骨架**
  - 做：`pyproject.toml` 增 `fastapi`/`uvicorn`/`openai`/`python-dotenv`/`pytest`；建 `app/config.py`（读 `.env`：`DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL`/`MODEL`，及 `data/` 目录路径）；建 `.env.example`。
  - 产出：`app/config.py`、`.env.example`、更新后的 `pyproject.toml`（另加 `app/__init__.py`、`tests/test_config.py`、`backend/.env`，均不违反契约）。
  - 验证：`uv run python -c "from app.config import settings; print(settings.model)"` 输出 `deepseek-flash`。
  - 实测：`uv sync` 装依赖（openai 解析为 3.28.0）；`uv run pytest -q` → `4 passed`；验证命令输出 `deepseek-flash`。（注：`MODEL=deepseek-flash` 的 ID 有效性问题见 Plan §12 #7，T1.2 真实调用时再确认。）

- [x] **T1.2 LLM 抽象层**
  - 做：`app/llm.py` 按 Plan §5 实现 `LLMClient` Protocol 与 `DeepSeekClient`（`chat` / `chat_stream`，OpenAI SDK + `base_url`）。
  - 产出：`app/llm.py`（另加 `tests/test_llm.py`，mock OpenAI、不真实计费）。
  - 验证：手写脚本调 `chat("ping")` 返回字符串；`chat_stream` 能逐块 `yield` 非空增量。
  - 实测：`uv run pytest -q` → `14 passed`（含 10 个 LLM 单测）；真实调用 `chat` 返回 `str`（`pong 🏓 …`），`chat_stream` 逐块产出非空增量 `'hello'`；确认 openai 3.28.0 的 `chat.completions.create` 支持 `model/messages/stream/response_format`，`MODEL=deepseek-flash` 真实可用（Plan §12 #7 风险消除）。

- [ ] **T1.3 SQLite 记忆层骨架**
  - 做：`app/memory.py` 建库建表（`concepts`/`user_profile`/`notes`/`questions`/`annotations`，DDL 见 Plan §3）；初始化 `PRAGMA journal_mode=WAL`；按 Plan §7「DB 访问约定」实现**短连接 + 写操作全局锁**；`user_profile` 初始化 id=1 空背景。
  - 产出：`app/memory.py`、`data/asktonote.db`。
  - 验证：首次运行自动建库；`sqlite3` 打开后 5 张表结构与 Plan §3 一致。

- [ ] **T1.4 笔记文件读写**
  - 做：`app/notes.py` 实现读文件、创建文件（先写 `# {title}`）、末尾追加 Q/A 区块；**目录不存在时自动创建 `data/notes/`**；加**模块级文件写锁**，且锁**覆盖"读→改→写"整段**（Plan §7）。
  - 产出：`app/notes.py`。
  - 验证：连续两次追加，文件内容为 `# 标题` + 两个 `## 问题` 区块，无覆盖。

- [ ] **T1.5 段落定位插入**
  - 做：`app/notes.py` 实现定位算法（Plan §4.1）：按空行切段 → 源码 `find(anchor)` 优先 → 逐段脱标记兜底 → 仅命中标题行时改取该标题下首个正文段 → 未命中报错；支持 `depth=deep` 时在锚点段落后插入 `####` 新章节。
  - 产出：定位/插入函数 + `tests/test_notes.py`。
  - 验证：`uv run pytest tests/test_notes.py` 通过（覆盖：源码命中→词后；跨行内标记→段末；重复取首次；未命中报错；deep 插 `####`）。

- [ ] **T1.6 模式①作答智能体 + Prompt**
  - 做：`app/prompts.py` 落 Plan §6.1 模板（占位符按 Plan §6 渲染约定）；`app/agents/answer_note.py` 实现模式①：组装【场景/背景/已掌握/尚不熟悉/其他主题/当前笔记】→ `chat_stream` 输出**纯 Markdown**。
  - 产出：`app/prompts.py`、`app/agents/answer_note.py`。
  - 验证：直接调用该函数能流式产出 Markdown 文本，无 JSON/代码块包裹。

- [ ] **T1.7 回答落盘 + SSE 接口**
  - 做：`app/api.py` + `main.py` 挂载路由与 CORS；`POST /api/answer` 按 Plan §4.1 返回 SSE（`data: {JSON}`，`type` = `delta`/`done`/`error`）；流结束执行 Plan §7 的**正文清洗**（行首标题一律压为 `###`、删开头重复问题标题、去整体代码围栏）→ 组 `## {问题}` + 正文**追加/新建文件**（新 slug 先写 `# {title}`；**已存在则 `title` 以 `notes` 表为准**）→ 写 `notes` 记录（新建写 `created_at`，两种情况**均刷新 `updated_at`**）→ 写 `questions(source='initial')`（`concept` 置 NULL）→ 发 `done{note_markdown}`。
  - 产出：`app/api.py`、`main.py`。
  - 验证：`curl -N` 能逐条看到 `delta`，末条 `done` 带 `note_markdown`，磁盘生成 `data/notes/{slug}.md`，`notes`/`questions` 表各增一行。（`slug`/`title` 可用 T1.10 的模式A 取得，或 curl 手动传。）

- [ ] **T1.8 JSON 输出约束 + 解析容错 + 单测收口**
  - 做：`llm.py` 的 `chat` 加 `json_mode` 参数（带 `response_format={"type":"json_object"}`，Plan §5）；定义四个 JSON 调用的 Pydantic 模型（模式A/模式B/模式②/背景推断，schema 见 Plan §6）；解析容错工具（剥离 ```` ```json ```` 包裹、容忍多余文字）+ 校验不过**回喂错误重试 1 次** + 字段缺失默认值兜底（Plan §7「JSON 输出约束」）；`tests/test_parse.py`；跑通全部后端单测。
  - 产出：`llm.py` 的 `json_mode`、Pydantic 模型、解析/校验工具、`tests/test_parse.py`。
  - 验证：`uv run pytest` 全绿，覆盖"带 ```json 围栏 / 带多余文字 / 字段缺失"三类用例。

- [ ] **T1.9 读接口（笔记 / 主题列表）**
  - 做：`GET /api/note/{slug}`、`GET /api/notes` 按 Plan §4.1 实现（`notes` 返回 `[{slug,title}]`，**按 `updated_at` 倒序**、最新在前）。
  - 产出：`app/api.py` 两个读路由。
  - 验证：curl 能取到笔记全文；`/api/notes` 返回已落盘主题列表。

- [ ] **T1.10 模式A 接口（基础版）**
  - 做：`prompts.py` 落 Plan §6.3 模板；`question_optimizer.py` 实现模式A 方法；`POST /api/optimize/initial`：入参 `{question, slug?}` → 出参 `{optimized_question, slug, title}`（组装时按 Plan §6 渲染约定注入背景与两类概念）。**注**：主题边界判定与 slug 合法性校验的完善在 **M5 T5.1**。
  - 产出：`app/agents/question_optimizer.py`（模式A）、模式A Prompt、`app/api.py` 路由。
  - 验证：传 `{question}` 返回结构符合契约、字段齐全（JSON 解析容错生效）。

---

## M2 前端骨架

**验证标准（Plan §10）**：浏览器能提问、确认优化问题与主题、实时看回答流入笔记。

- [ ] **T2.1 前端工程初始化**
  - 做：`frontend/` 建 Vite + React + TS + zustand；`vite.config.ts` 配 `server.proxy` 把 `/api` 转发到 `http://127.0.0.1:8000`（后端 CORS 已在 T1.7 挂好）。
  - 产出：`frontend/` 工程、`vite.config.ts`。
  - 验证：`npm run dev` 起服务，浏览器能通过 proxy 访问后端接口。

- [ ] **T2.2 前端契约与请求层**
  - 做：`src/types.ts` 与后端 JSON 契约对齐；`src/api.ts` 封装 `fetch`，用 `ReadableStream` 读 SSE（按 `type` 分发 `delta`/`done`/`error`，EventSource 不支持 POST）；**必须按行缓冲解析**——chunk 是任意切分的字节段，先 `TextDecoder({stream:true})` 解码并追加到 buffer，按 `\n\n` 切出完整事件再 `JSON.parse`，尾部残片留待下一 chunk，**严禁对每个 chunk 直接 parse**。
  - 产出：`src/types.ts`、`src/api.ts`。
  - 验证：控制台能收到并打印 `delta`、`done`；长回答与中文内容下无 JSON 解析错误/乱码（跨 chunk 切分正确）。

- [ ] **T2.3 状态仓库**
  - 做：`src/store.ts`（zustand：`noteSlug`/`noteMarkdown`/`streaming`/`optimizeCard`/`annotations`/`pendingQuestions` + Plan §8 状态机）。
  - 产出：`src/store.ts`。
  - 验证：状态能在组件间读写、切换，状态机流转与 Plan §8 一致。

- [ ] **T2.4 笔记区渲染 + 启动加载**
  - 做：`src/components/NoteView.tsx` 用 `react-markdown` 渲染；`src/remark-wikilink.ts` 高亮 `[[概念名]]` / `[[slug#概念]]`（**v1 不可点击跳转**）；页面初始化调 `GET /api/notes`（**按 `updated_at` 倒序**）→ 有主题则自动打开最近更新的一篇（"继续上次学习"），无主题显示空态、引导提问。
  - 产出：`NoteView.tsx`、`remark-wikilink.ts`、启动加载逻辑。
  - 验证：笔记 MD 正常渲染，内链显示为高亮标签；已有主题时刷新页面自动打开最近更新的一篇。

- [ ] **T2.5 提问区 + 问题确认卡（模式A）**
  - 做：`AskBar.tsx`（输入原始问题 +「优化」）与 `QuestionConfirm.tsx`（确认卡：**优化后问题可编辑**；主题只读显示"并入《title》/新建《title》"，`slug` 不可改）→ 确认发起。
  - 产出：`AskBar.tsx`、`QuestionConfirm.tsx`。
  - 验证：提问 → 弹出确认卡 → 改问题 → 确认后发出。

- [ ] **T2.6 流式打字机（笔记区底部实时渲染 + done 覆盖校正）**
  - 做：`delta` 直接在**笔记区底部**累积渲染出当前新 Q/A 块；`done` 用后端 `note_markdown` **覆盖校正**；`streaming` 期间禁止标注。
  - 产出：流式渲染逻辑接入 `NoteView` + `store`。
  - 验证：浏览器提问后，回答实时流入笔记区，结束后被校正一次。

---

## M3 标注 + 问题确认 + 深度

**验证标准（Plan §10）**：标注后生成候选问题、确认深度、笔记原地扩写。

- [ ] **T3.1 模式②扩写智能体 + Prompt**
  - 做：`prompts.py` 落 Plan §6.2 模板；`answer_note.py` 加模式②方法：输入 `note_markdown`/`anchor`/`question`/`kind`/`concept`/`depth` + 背景与两类概念 → 输出 JSON `{insert:{insert_text}, new_section?}`（`brief` 无 `new_section`）。
  - 产出：模式② Prompt 与方法。
  - 验证：`brief`/`deep` 各调一次，返回结构符合契约且解析成功。

- [ ] **T3.2 模式B 接口（标注 → 候选问题）**
  - 做：`prompts.py` 落 Plan §6.4 模板；`question_optimizer.py` 实现模式B 方法；`POST /api/optimize/annotation`：入参 `{snippets[], note_markdown}` → 出参 `{questions:[{text,concept,kind,reason}]}`（**一标注一问题、不合并**）；组装时注入 `{concepts_existing}`——读 `concepts` 表**仅名称清单**（Plan §6.4/§7），使模式B **优先复用已有概念名**。
  - 产出：模式B 路由 + Prompt + `app/agents/question_optimizer.py` 模式B 方法。
  - 验证：传多个 snippet，返回条数一致、每项字段齐全。

- [ ] **T3.3 标注落盘接口**
  - 做：`POST /api/annotate` 按 Plan §7：运维顺序 = 读笔记 → 模式② → 定位插入（`brief` 插简释 / `deep` 加 `####` 章节，其 `content` 行首标题压为 `###`）→ 写 `annotations`（`note_id`/`snippet`/`concept`/`depth`）与 `questions(source='annotation')` → 返回更新后 `note_markdown` → 刷新 `notes.updated_at`。**本里程碑先不写概念记忆**（Plan §7 步骤3 的 `concepts`/`pending` 写入由 **M4 T4.6** 接入；M3 验证标准只要求"笔记原地扩写"）。
  - 产出：`app/api.py` 路由。
  - 验证：curl 标注一次，笔记在锚点后出现解释（deep 另有 `####` 章节），`annotations`/`questions` 各增一行。

- [ ] **T3.4 划词标注（前端）**
  - 做：`NoteView` 在**流式结束且 MD 渲染稳定**后用 `window.getSelection()` 捕获选中文本，仅限**笔记区**，写入 `annotations[]`。
  - 产出：划词逻辑 + 标注气泡/列表。
  - 验证：流式期间无法划词；结束后可选中并出现在标注列表。

- [ ] **T3.5 候选问题确认区（标注态）**
  - 做：划词结束后**整批一次性**调模式B，同一确认区切换为候选问题列表（可删减/编辑），每条选**简单/深入**；**下钻的新标注单独再走一遍模式B**并**插到队列最前**。
  - 产出：`QuestionConfirm.tsx` 标注态。
  - 验证：多标注并行展示；下钻标注优先排到队首。

- [ ] **T3.6 一次一个串行执行**
  - 做：确认一条 → `POST /api/annotate` → 刷新笔记 → 用户读 → 点「已理解」→ 下一条（或点「跳过」→ **前端纯本地动作**：不调 `confirm`、记忆不变、不回滚扩写，直接下一条）；**单条失败即中断**，前端提示**该条重试**（不推进队列）；每次以最新 `note_markdown` 更新状态。（「已理解」按钮调 `POST /api/memory/confirm` 的接线见 **M4 T4.6**。）
  - 产出：串行执行逻辑 + 失败提示。
  - 验证：故意让某条失败，队列停在该条、提示重试，后续条不执行；点「跳过」后队列推进、该概念分数不变。

---

## M4 记忆

**验证标准（Plan §10）**：记忆随标注/确认更新，并影响下次回答。

- [ ] **T4.1 掌握度规则 + 单测**
  - 做：`memory.py` 实现 Plan §7 规则：被标注**不改分**（不在库 → `score=10`）且置 `pending=1`（**多次标注只置一次**）；`mastered` = `score≥80`；等级由 score 推导；`tests/test_memory.py`。
  - 产出：规则函数 + `tests/test_memory.py`。
  - 验证：`uv run pytest tests/test_memory.py` 通过（含"已在库被标注不改分"用例）。

- [ ] **T4.2 记忆读接口 + 已理解确认**
  - 做：`GET /api/memory` → `{background, concepts:[{name,score,level}]}`（`level` 后端推导，`pending` **不返回**）；`POST /api/memory/confirm` → 仅 `pending=1` 时 `pending=0`、`score=min(100, score+10)`，否则原样返回（**幂等**）。
  - 产出：两个路由。
  - 验证：同一概念连点两次 confirm，分数只 +10；返回体不含 `pending`。

- [ ] **T4.3 背景写入与刷新**
  - 做：`POST /api/memory`（带 `background` 则写库并置 `background_locked=1`）；`POST /api/memory/refresh`（会话级一次：读概念按 `score≥50`/`<50` 分两类 + 主题清单**仅标题** → 背景推断 Prompt → `background_locked=0` 才写回）；落 Plan §6.5 背景推断 Prompt。
  - 产出：两个路由 + 背景推断 Prompt。
  - 验证：首次（概念为空）返回空串；手改背景后再 refresh 不被覆盖。

- [ ] **T4.4 记忆影响下次回答**
  - 做：复核 `answer_note.py`/`question_optimizer.py` 的 Prompt 组装**确实读 `concepts` 表**（M1 时该表为空 → 传空列表），修正任何硬编码/遗漏。
  - 产出：Prompt 组装收口（无新接口）。
  - 验证：造两条记忆（一条 ≥50、一条 <50）后，**直接调用 Prompt 组装函数**可见两类分别进入 Prompt。

- [ ] **T4.5 设置页 + 会话级刷新**
  - 做：`SettingsPanel.tsx` 显示当前背景、可编辑覆盖、提供"刷新背景"按钮；**页面加载/刷新时**调一次 `/api/memory/refresh`（长会话中途不重算）。
  - 产出：`SettingsPanel.tsx` + 启动刷新逻辑。
  - 验证：刷新页面触发一次 refresh；手改背景后自动推断不再覆盖。

- [ ] **T4.6 记忆写入接线（标注 + 已理解）**
  - 做：把 T4.1 的规则接到两处——①`POST /api/annotate` 步骤3：`concept`/`kind` 取自请求，概念**分数不变**、**不在库 → `score=10`**、两种情况均置 `pending=1`（**多次标注只置一次**）；②前端「已理解」按钮 → `POST /api/memory/confirm`，返回后进入下一条（接 T3.6）。
  - 产出：`app/api.py` 接线 + 前端「已理解」调用。
  - 验证：标注一个概念后 `concepts` 出现该概念且 `pending=1`；点「已理解」→ `pending=0`、`10→20`；**同一概念再标注一次，确认总分只 +10**（幂等）。

---

## M5 主题边界

**验证标准（Plan §10）**：springmvc vs flux 并行主题正向引用；新主题文件正确创建。
> 边界说明：新主题的**落盘机制**（新建文件 + `notes` 记录）已在 **T1.7** 实现，本里程碑只做**判定**（T5.1）与**跨主题内链**（T5.2）。

- [ ] **T5.1 主题边界判定 + slug 校验**
  - 做：模式A 完善新主题判定（关联弱 → 新 `slug`/`title`）；后端校验 `slug` 合法性 + 去重：**非法字符 → 回退为并入当前主题**；重复 → 按"已存在→追加"处理（Plan §12 #1，**不做自动后缀**）。
  - 产出：模式A 判定逻辑 + 校验函数。
  - 验证：构造非法 slug 请求，确认回退并入当前主题且不崩。

- [ ] **T5.2 跨主题正向内链**
  - 做：模式① Prompt 注入【其他主题】清单（`slug: title`），要求跨主题概念用 `[[slug#概念]]`、本主题用 `[[概念名]]`（高亮渲染已由 **T2.4** 覆盖，**不改前端**）。
  - 产出：模式① Prompt 与组装收口。
  - 验证：springmvc 笔记中引用 flux 概念时输出 `[[flux#概念]]`，前端渲染为高亮标签且不可点击。

---

## 端到端验收（手动，对齐 Spec §3.1 目标）

- [ ] 页面刷新后自动打开最近更新的主题（"继续上次学习"）；无主题时显示空态引导。
- [ ] 提问 → 模式A 确认（问题可改、slug 只读）→ 流式回答进笔记，形成 `## 问题` 区块。
- [ ] 划词标注多个陌生点 → 模式B 出候选问题 → 逐条选**简单/深入** → 笔记**原地扩写**（deep 含 `####` 章节）。
- [ ] 点「已理解」→ 记忆 `pending` 清、`score+10`；重复点击不叠加。
- [ ] 点「跳过」→ 不调 `confirm`、记忆不变、队列推进到下一条（已写入的扩写不回滚）。
- [ ] 重复提问与标注，同一主题追加、新主题新建；跨主题正向内链渲染正常。
- [ ] 二次会话背景推断生效，且回答深度随掌握度变化。