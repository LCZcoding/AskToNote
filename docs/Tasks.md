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

- [x] **T1.3 SQLite 记忆层骨架**
  - 做：`app/memory.py` 建库建表（`concepts`/`user_profile`/`notes`/`questions`/`annotations`，DDL 见 Plan §3）；初始化 `PRAGMA journal_mode=WAL`；按 Plan §7「DB 访问约定」实现**短连接 + 写操作全局锁**；`user_profile` 初始化 id=1 空背景。
  - 产出：`app/memory.py`、`data/asktonote.db`（另加 `tests/test_memory.py`；`data/` 为运行期产物，见下「实测」说明）。
  - 验证：首次运行自动建库；`sqlite3` 打开后 5 张表结构与 Plan §3 一致。
  - 实测：`uv run pytest -q` → `24 passed`（新增 10 个 memory 用例，覆盖建库/表结构/默认值/WAL/唯一约束/幂等/短连接+写锁）；真实建库 `sqlite3` 校验 → 表 `{concepts,user_profile,notes,questions,annotations}` 齐全、`journal_mode=wal`、`user_profile=[(1,'',0)]`。（注：`backend/data/` 为运行期产物，是否纳入 `.gitignore` 待确认。）

- [x] **T1.4 笔记文件读写**
  - 做：`app/notes.py` 实现读文件、创建文件（先写 `# {title}`）、末尾追加 Q/A 区块；**目录不存在时自动创建 `data/notes/`**；加**模块级文件写锁**，且锁**覆盖"读→改→写"整段**（Plan §7）。
  - 产出：`app/notes.py`（另加 `tests/test_notes.py`）。
  - 验证：连续两次追加，文件内容为 `# 标题` + 两个 `## 问题` 区块，无覆盖。
  - 实测：`uv run pytest -q` → `30 passed`（新增 6 个 notes 用例）；真实运行连续两次追加 → `'# 标题\n\n## Q1\n\nA1\n\n## Q2\n\nA2\n'`，标题 + 两个 `## 问题` 区块、无覆盖；`read_note` 回读一致。

- [x] **T1.5 段落定位插入**
  - 做：`app/notes.py` 实现定位算法（Plan §4.1）：按空行切段 → 源码 `find(anchor)` 优先 → 逐段脱标记兜底 → 仅命中标题行时改取该标题下首个正文段 → 未命中报错；支持 `depth=deep` 时在锚点段落后插入 `####` 新章节。
  - 产出：`app/notes.py` 的 `AnchorNotFoundError`/`apply_annotation`（纯函数）/`insert_annotation`（复用 `_file_lock`，锁覆盖读→改→写）+ `tests/test_notes.py`。
  - 验证：`uv run pytest tests/test_notes.py` 通过（覆盖：源码命中→词后；跨行内标记→段末；重复取首次；未命中报错；deep 插 `####`）。
  - 实测：`uv run pytest tests/test_notes.py -q` → `15 passed`（新增 9 个定位用例）；`uv run pytest -q` → `39 passed`。覆盖：源码命中→词后、跨行内标记（`**容器**和**镜像**`）→段末、重复 anchor 取首次、未命中/空 anchor 报错、仅命中标题行→改取标题下首个正文段段末、`depth=deep` 段后插 `####` 且新章节 `content` 行首标题压为 `###`、文件级插入落盘且回读一致。

- [x] **T1.6 模式①作答智能体 + Prompt**
  - 做：`app/prompts.py` 落 Plan §6.1 模板（占位符按 Plan §6 渲染约定）；`app/agents/answer_note.py` 实现模式①：组装【场景/背景/已掌握/尚不熟悉/其他主题/当前笔记】→ `chat_stream` 输出**纯 Markdown**。
  - 产出：`app/prompts.py`、`app/agents/answer_note.py`（另加 `app/agents/__init__.py`、`tests/test_answer_note.py`）。
  - 验证：直接调用该函数能流式产出 Markdown 文本，无 JSON/代码块包裹。
  - 实测：`uv run pytest -q` → `43 passed`（新增 4 个用例，mock LLM）；真实调用 `answer_note_stream` → 1314 个增量、共 2808 字，输出纯 Markdown（首行 `### …`，无 JSON、无代码围栏）。空值统一渲染「（暂无）」、`{slug}` 空前缀保留。（占位符替换用 `str.replace`，规避后续 JSON 模板的花括号冲突。）

- [x] **T1.7 回答落盘 + SSE 接口**
  - 做：`app/api.py` + `main.py` 挂载路由与 CORS；`POST /api/answer` 按 Plan §4.1 返回 SSE（`data: {JSON}`，`type` = `delta`/`done`/`error`）；流结束执行 Plan §7 的**正文清洗**（行首标题一律压为 `###`、删开头重复问题标题、去整体代码围栏）→ 组 `## {问题}` + 正文**追加/新建文件**（新 slug 先写 `# {title}`；**已存在则 `title` 以 `notes` 表为准**）→ 写 `notes` 记录（新建写 `created_at`，两种情况**均刷新 `updated_at`**）→ 写 `questions(source='initial')`（`concept` 置 NULL）→ 发 `done{note_markdown}`。
  - 产出：`app/api.py`、`main.py`（另加 `tests/test_api.py`；`app/memory.py` 增加读/写 helper：`level_from_score`/`get_background`/`list_concepts`/`concepts_by_mastery`/`list_notes`/`get_note_by_slug`/`insert_note`/`touch_note`/`insert_question`）。
  - 验证：`curl -N` 能逐条看到 `delta`，末条 `done` 带 `note_markdown`，磁盘生成 `data/notes/{slug}.md`，`notes`/`questions` 表各增一行。（`slug`/`title` 可用 T1.10 的模式A 取得，或 curl 手动传。）
  - 实测：`uv run pytest -q` → `52 passed`（新增 9 个用例：`test_api` 7 + `compress_headings` 围栏感知 1 + `_clean_body` 围栏内保留 1；真实 curl 验证另见下）。真实 `curl.exe -N POST /api/answer`（端口 8001）→ 2194 个 `delta` + 末条 `done`（带 `note_markdown`）；磁盘生成 `data/notes/docker.md`（16059 字节）；`notes` 1 行、`questions` 2 行（两次请求，`source=initial`、`concept=NULL`）。**真实验证暴露并已修**：正文清洗曾把**代码围栏内的 `#` 注释**（如 bash）也压成 `###`，破坏代码示例 → 经用户确认选 **A（跳过围栏内）**：先改 Plan §7 措辞（“代码围栏内的行除外”），再令 `compress_headings` 识别 ``` / ~~~ 围栏、围栏内原样保留（`_clean_body` 顺序调整为“先压标题、后去整体围栏、再删重复标题”）。


- [x] **T1.8 JSON 输出约束 + 解析容错 + 单测收口**
  - 做：`llm.py` 的 `chat` 加 `json_mode` 参数（带 `response_format={"type":"json_object"}`，Plan §5）；定义四个 JSON 调用的 Pydantic 模型（模式A/模式B/模式②/背景推断，schema 见 Plan §6）；解析容错工具（剥离 ```` ```json ```` 包裹、容忍多余文字）+ 校验不过**回喂错误重试 1 次** + 字段缺失默认值兜底（Plan §7「JSON 输出约束」）；`tests/test_parse.py`；跑通全部后端单测。
  - 产出：`app/parse.py`（四个 Pydantic 模型 + `extract_json`/`parse_model`/`chat_json` + `JsonParseError`；同步在 Plan §2 目录清单补该文件）、`tests/test_parse.py`。（`llm.py` 的 `json_mode`/`response_format` 已在 T1.2 落地，无需改动。）
  - 验证：`uv run pytest` 全绿，覆盖"带 ```json 围栏 / 带多余文字 / 字段缺失"三类用例。
  - 实测：`uv run pytest -q` → `68 passed`（新增 16 个 `test_parse` 用例）。覆盖：纯 JSON / ```` ```json ```` 围栏 / 无语言围栏 / 前后多余文字提取；四类模型解析；`brief` 无 `new_section`（默认 None）、`deep` 有；非关键字段（`concept`/`kind`/`reason`/`background`）缺失默认值；`kind` 非枚举与关键字段缺失抛 `ValidationError`；`chat_json` 以 `json_mode=True` 调用；首次非法 → 回喂错误重试 1 次成功（第二次 `user` 含原始问题与错误反馈）；两次失败抛 `JsonParseError` 且恰好调用 2 次；校验失败（非语法错）同样触发重试。


- [x] **T1.9 读接口（笔记 / 主题列表）**
  - 做：`GET /api/note/{slug}`、`GET /api/notes` 按 Plan §4.1 实现（`notes` 返回 `[{slug,title}]`，**按 `updated_at` 倒序**、最新在前）。
  - 产出：`app/api.py` 两个读路由（另加 `tests/test_api.py` 4 个用例）。
  - 验证：curl 能取到笔记全文；`/api/notes` 返回已落盘主题列表。
  - 实测：`uv run pytest -q` → `72 passed`（新增 4 个读接口用例：笔记全文一致、未知主题 404、按 `updated_at` 倒序且仅暴露 `slug`/`title`、空库返回 `[]`）。真实 HTTP 冒烟（`uvicorn --port 8001` + `curl.exe`）：`GET /api/notes` → `[{"slug":"smoke","title":"SMOKE 主题"}]`；`GET /api/note/smoke` → `{"slug":"smoke","title":"SMOKE 主题","markdown":"# SMOKE 主题\n\n## SMOKE 问题\n\nSMOKE 正文。\n"}`；未知 slug → `404`。冒烟产物已清理（文件与 `notes`/`questions` 行删除）。


- [x] **T1.10 模式A 接口（基础版）**
  - 做：`prompts.py` 落 Plan §6.3 模板；`question_optimizer.py` 实现模式A 方法；`POST /api/optimize/initial`：入参 `{question, slug?}` → 出参 `{optimized_question, slug, title}`（组装时按 Plan §6 渲染约定注入背景与两类概念）。**注**：主题边界判定与 slug 合法性校验的完善在 **M5 T5.1**。
  - 产出：`app/agents/question_optimizer.py`（模式A）、模式A Prompt（`prompts.py`）、`app/api.py` 路由（另加 `tests/test_question_optimizer.py`、`test_api.py` 各 4 用例）。**前置**：Plan §6 补 `【用户问题】{question}`（见 T1.11 背景）。
  - 验证：传 `{question}` 返回结构符合契约、字段齐全（JSON 解析容错生效）。
  - 实测：`uv run pytest -q` → `80 passed`（新增 8 个用例：Prompt 渲染 2、智能体 2、路由 4）。覆盖：问题注入 Prompt、空 slug 原样保留（不替换「（暂无）」）、空背景/概念/主题统一「（暂无）」、`json_mode=True` 调用、非法 JSON 回喂重试 1 次成功、两次失败 → `502`、`【现有主题】`含全部主题（不排除当前，供 slug 去重）。真实调用 `optimize_initial`（slug 空 = 新主题）→ 返回 `{optimized_question: "Docker 中的「容器（container）」到底是什么？…", slug: "docker", title: "Docker 容器基础"}`，字段齐全、JSON 约束生效。


- [x] **T1.11 修复：模式①把用户问题传入 LLM（回归）**
  - 做：Plan §6.1 已补 `【用户问题】{question}` 行；同步 `prompts.py` 的 `ANSWER_NOTE_TEMPLATE` 补该行、`render_answer_note_prompt` 加 `question` 参数（原样渲染、不走「（暂无）」）、`answer_note.py` 的 `answer_note_stream` 加 `question` 参数、`api.py` 的 `/api/answer` 传入 `req.question`；补/改测试断言问题进入作答 Prompt。
  - 背景：T1.6/T1.7 落地时 Plan §6.1 缺 `{question}` 占位符，导致**作答 Prompt 未包含用户问题**（新主题首问 `note_markdown` 为空时尤其失真）。属**回归修复**，与 T1.10 分开提交。
  - 产出：`app/prompts.py`、`app/agents/answer_note.py`、`app/api.py`（+ 相应测试）。
  - 验证：`uv run pytest` 全绿；作答 Prompt 中可见 `【用户问题】…`。
  - 实测：`uv run pytest -q` → `80 passed`（改 3 处断言：模板占位符全填充含 `【用户问题】`、流式函数透传 `question` 进 user prompt、`/api/answer` 的 Prompt 含 `【用户问题】Q`）。真实调用 `answer_note_stream(question="什么是 Docker 容器？")` → 894 增量、1732 字，**首句直接切题**（"[[Docker]] 容器是一个轻量的、自给自足的『运行包裹』…"），修复前该 Prompt 不含问题、无法切题。


- [x] **T1.12 大块追问的引用片段（`context`）能力（插队）**
  - 做：`prompts.py` 的 `ANSWER_NOTE_TEMPLATE`（§6.1）与 `OPTIMIZE_INITIAL_TEMPLATE`（§6.3）各补 `【引用片段】{context}` 行；`render_answer_note_prompt`/`render_optimize_initial_prompt` 加 `context` 参数（空 → 「（暂无）」）；`answer_note.py`/`question_optimizer.py` 透传 `context`；`api.py` 的 `OptimizeInitialRequest` 与 `/api/answer` 请求体各加**可选字段 `context: str = ""`** 并传入。**不碰**落盘逻辑（追问仍按 FR-3b 末尾追加）。
  - 背景：Spec FR-4a/FR-4b —— 划词选区（去首尾空白）> 60 字退化为「追问」，引用片段**只进 Prompt、不进 `## 问题` 标题**。
  - 产出：`app/prompts.py`、`app/agents/answer_note.py`、`app/agents/question_optimizer.py`、`app/api.py`（+ `tests/test_answer_note.py`/`test_question_optimizer.py`/`test_api.py` 断言）。
  - 验证：`uv run pytest` 全绿；传 `context` 时两处 Prompt 均含【引用片段】、不传时渲染「（暂无）」；出参与 `## 问题` 标题不受 `context` 影响。
  - 实测：`uv run pytest -q` → `82 passed`（+2 例：`test_answer_context_only_enters_prompt_not_question_heading`、`test_optimize_initial_passes_context_into_prompt`；并改 `test_answer_note`/`test_question_optimizer` 的占位符与 NA 计数断言）。真实调用：`POST /api/optimize/initial`（带 `context`）→ 200，优化问题切中引用片段；`curl.exe -N POST /api/answer`（带 `context`）→ `delta*`→`done`，回答围绕引用片段（原文以引用块呈现），落盘笔记标题为 `## (验证) 这段怎么理解`（**不含** `context`）。验证写入的临时主题已清理（文件 + `notes`/`questions` 行）。


- [x] **T1.13 标注双向跳转标记（写入笔记源码，插队）**
  - 做：`app/notes.py` 的 `apply_annotation` 改造——原词**源码精确命中**时，词前插 `<a id="anno-{n}-src"></a>` 并把原词包成 `[原词](#anno-{n})`；解释处插 `<a id="anno-{n}"></a>`，并在简释末尾追加回跳 `[↩](#anno-{n}-src)`；`n = uuid4().hex[:8]`。**降级**：仅脱标记兜底命中、或原词已在链接/标记内 → **只插解释侧锚点**、不包原词、不加回跳。同步 `tests/test_notes.py`。
  - 背景：Spec FR-9c —— 原词 ⇄ 解释 **双向**可点击跳转，**写入源码**、通用 MD 阅读器可点。
  - 产出：`app/notes.py`（+ `tests/test_notes.py`）。
  - 验证：`uv run pytest tests/test_notes.py` 通过（源码命中 → 双向锚点/链接齐全；兜底命中 → 单向降级）。
  - 实测：`uv run pytest -q` → `84 passed`（+2 例：`test_source_hit_inside_markup_degrades_to_one_way`、`test_source_hit_inside_wikilink_degrades_to_one_way`；并改 T1.5 原 6 例断言为双向/单向结构）。`_locate` 增返「命中方式 + 原词起点」，`apply_annotation` 据此判定双向；新增 `_is_in_markup`（识别 `[[内链]]`/`[...](...)`/行内代码/粗体）做降级判定。真实调用 `apply_annotation` → 源码命中输出 `<a id="anno-{n}-src"></a>[轻量化](#anno-{n})<a id="anno-{n}"></a>（即共享宿主内核）[↩](#anno-{n}-src)`（与 Plan §4.1 例一致）；兜底/在标记内 → **只插解释侧锚点于段末**（Plan §4.1 降级行已同步注明「统一段末」，避免切断 `**原词**`/`[[原词]]`）。


---

## M2 前端骨架

**验证标准（Plan §10）**：浏览器能提问、确认优化问题与主题、实时看回答流入笔记。

- [x] **T2.1 前端工程初始化**
  - 做：`frontend/` 建 Vite + React + TS + zustand；`vite.config.ts` 配 `server.proxy` 把 `/api` 转发到 `http://127.0.0.1:8000`（后端 CORS 已在 T1.7 挂好）。
  - 产出：`frontend/` 工程、`vite.config.ts`。
  - 验证：`npm run dev` 起服务，浏览器能通过 proxy 访问后端接口。
  - 实测：脚手架（Vite8 + React19 + TS + zustand + react-markdown）已存在；本轮把 `vite.config.ts` 代理目标由 `http://localhost:8000` 改为 `http://127.0.0.1:8000`（规避 localhost→IPv6）。`npm install`（npmmirror）→ up to date；`npm run dev` → Vite ready（:5173）；`uv run uvicorn main:app` → :8000 up。`curl.exe http://localhost:5173/api/notes` → **200 `[]`**（与直连 `127.0.0.1:8000` 一致），proxy 生效；验证后两服务已停止。

- [x] **T2.2 前端契约与请求层**
  - 做：`src/types.ts` 与后端 JSON 契约对齐；`src/api.ts` 封装 `fetch`，用 `ReadableStream` 读 SSE（按 `type` 分发 `delta`/`done`/`error`，EventSource 不支持 POST）；**必须按行缓冲解析**——chunk 是任意切分的字节段，先 `TextDecoder({stream:true})` 解码并追加到 buffer，按 `\n\n` 切出完整事件再 `JSON.parse`，尾部残片留待下一 chunk，**严禁对每个 chunk 直接 parse**。
  - 产出：`src/types.ts`、`src/api.ts`。
  - 验证：控制台能收到并打印 `delta`、`done`；长回答与中文内容下无 JSON 解析错误/乱码（跨 chunk 切分正确）。
  - 实测：`npm run build`（`tsc -b && vite build`）→ 通过（20 modules，产出 `dist/`）。`types.ts` 按 Plan §4.0/§4.1 覆盖全部 10 个接口的请求/响应 + `SseEvent` 联合类型（`delta`/`done`/`error`）+ `Kind`/`Depth`/`Level` 枚举联合；`api.ts` 提供 `getNotes`/`getNote`/`getMemory`/`optimizeInitial`/`optimizeAnnotation`/`annotate`/`updateMemory`/`confirmConcept`/`refreshBackground` 与 `streamAnswer`（SSE 按行缓冲：`TextDecoder({stream:true})` → 追加 buffer → 按 `\n\n` 切完整事件分发 → 残片留待下一 chunk；结束再 `decoder.decode()` flush 尾部残片；非 2xx/无流/网络异常统一走 `onError`）。**运行期冒烟**（临时在 `main.tsx` 挂 `streamAnswer` 调用 `POST /api/answer`，验证后已删除并回滚）：`uvicorn` :8000 + `npm run dev` :5173，浏览器加载 → 控制台 **95 条 `[smoke-delta]`**（中文正常、无乱码）+ **1 条 `[smoke-done]`**（笔记全文 3242 字，开头 `# 冒烟T2`），无 JSON 解析错误；后端日志恰 **1 次 `POST /api/answer 200 OK`**。（控制台另有一条 `net::ERR_ABORTED` 属浏览器导航期中止、未到达后端的空请求，非解析错误。）冒烟产物已清理（`data/notes/smoke-t2.md` 与 `notes`/`questions` 行删除）。

- [x] **T2.3 状态仓库**
  - 做：`src/store.ts`（zustand：`noteSlug`/`noteMarkdown`/`optimizeCard`/`annotations`/`pendingQuestions` + Plan §8 状态机）。**`streaming` 改为从 `phase` 派生的 selector（`useIsStreaming`），不单设布尔字段**——经与用户确认，避免"阶段"与"是否在流式"两份状态不同步。
  - 产出：`src/store.ts`。
  - 验证：状态能在组件间读写、切换，状态机流转与 Plan §8 一致。
  - 实测：`npm run build`（`tsc -b && vite build`）→ 通过。`phase: 'idle'|'optimizing'|'streaming'|'done'|'annotating'` 单一真相源；动作覆盖全链路 `startOptimize`→`setInitialCard`/`updateInitialQuestion`→`beginStreaming`/`appendDelta`/`finishStreaming`(覆盖校正)/`failStreaming`→`addAnnotation`/`removeAnnotation`→`startOptimize`→`setPendingQuestions`/`setAnnotationCard`/`updatePendingQuestion`/`removePendingQuestion`→`beginAnnotating`→`finishAnnotating`，另有 `cancelOptimize`/`openNote`/`clearAnnotations`；派生 selector `useIsStreaming`（=phase==='streaming'）与 `useCanAnnotate`（idle/done 两态可标注）。**确定性验证**：用本地 `rolldown` 把 `store.ts` 打成 ESM，Node 驱动全流程断言 **24 项全 PASS**（初始态/openNote/各阶段流转/字段透传/`finishStreaming` 覆盖校正/标注增删/候选问题默认 `depth=null` 与选深度/失败与取消分支）；验证脚本与产物已删除。串行执行游标/失败信息按确认**不在本任务引入**（留 T3.6）。

- [x] **T2.4 笔记区渲染 + 启动加载**
  - 做：`src/components/NoteView.tsx` 用 `react-markdown` 渲染（**开启 `rehype-raw`**，以渲染标注写入的行内 HTML 锚点与 `[↩]` 回跳，Spec FR-9c）；`src/remark-wikilink.ts` 高亮 `[[概念名]]` / `[[slug#概念]]`（**v1 不可点击跳转**）；页面初始化调 `GET /api/notes`（**按 `updated_at` 倒序**）→ 有主题则自动打开最近更新的一篇（"继续上次学习"），无主题显示空态、引导提问。
  - 产出：`NoteView.tsx`、`remark-wikilink.ts`、启动加载逻辑（另加 `NoteView.css`、`rehype-raw` 依赖，并重写 `App.tsx`/`App.css`/`index.css` 以替换 Vite 脚手架样式）。
  - 验证：笔记 MD 正常渲染，内链显示为高亮标签；**点击标注原词跳到其解释、点解释的「↩」跳回原词**；已有主题时刷新页面自动打开最近更新的一篇。
  - 实测：`npm run build`（`tsc -b && vite build`）→ 通过（222 modules）。`remark-wikilink.ts` 为 **remark 插件**（mdast 阶段把文本节点里的 `[[…]]` 换成 `html` 节点 `<span class="wikilink" data-slug="…">概念</span>`，交由 `rehype-raw` 解析成真实 span；`[[slug#概念]]` 取概念名做显示、slug 存入 `data-slug`/`title`；HTML 转义防注入）；`NoteView.tsx` = `react-markdown` + `rehypePlugins={[rehypeRaw]}` + `remarkPlugins={[remarkWikilink]}` 渲染 `store.noteMarkdown`；`App.tsx` 启动调 `GET /api/notes` → 有主题则 `GET /api/note/{slug}` 自动打开第一篇并 `openNote`，无主题显示空态、失败显示错误。**浏览器实测**（uvicorn :8000 + `npm run dev` :5173，预置一篇含 `[[容器]]`/`[[docker#镜像]]` 与双向锚点 `anno-ab12cd34` 的笔记）：① 启动**自动打开**该主题（未出现空态）；② `document.querySelectorAll('.wikilink')` → `SPAN|wikilink|容器`、`SPAN|wikilink|镜像`（内链高亮生效）；③ 点原词「命名空间」`location.hash` → `#anno-ab12cd34`、点「↩」→ `#anno-ab12cd34-src`（**双向跳转生效**，靠浏览器原生锚点导航、无额外 JS）；④ 控制台无 error/warning；⑤ 删除笔记后刷新 → 空态 `app-empty` 文案「还没有任何主题。…」正确。验证产物（临时笔记文件、`notes` 行、两个临时脚本）已清理。

- [x] **T2.5 提问区 + 问题确认卡（模式A）**
  - 做：`AskBar.tsx`（输入原始问题 +「优化」；**支持引用片段**——大块追问带入时在输入框上方显示引用文本，问题**可留空** → 默认「请详细讲解这段内容」，请求携带 `context`，见 **T1.12**/Spec FR-4b）与 `QuestionConfirm.tsx`（确认卡：**优化后问题可编辑**；主题只读显示"并入《title》/新建《title》"，`slug` 不可改）→ 确认发起。
  - 产出：`AskBar.tsx`、`QuestionConfirm.tsx`（另加 `AskBar.css`/`QuestionConfirm.css`、`store.ts` 新增 `pendingContext` 字段与 `setPendingContext`、`App.tsx`/`App.css` 接入底部提问区、Plan §8 同步 store 字段说明）。
  - 验证：提问 → 弹出确认卡 → 改问题 → 确认后发出；带引用片段提问时请求体含 `context`、问题留空走默认文案。
  - 实测：`npm run build` → 通过（226 modules）；`npm run lint` → 无告警。`AskBar`：引用片段非空时在输入框上方显示并可「移除」，问题可留空（有引用片段时用默认文案「请详细讲解这段内容」），`optimizeInitial({question,slug,context})` → `setInitialCard`；请求中/有确认卡待处理时禁用按钮，优化成功后清空输入。`QuestionConfirm`：**常驻渲染**（错误提示不随卡片消失），优化后问题可编辑、主题只读显示「并入/新建《title》」+ slug；确认 → `beginStreaming()` → 目标主题与当前不同则先 `getNote` 切换笔记区（404 则空）→ `streamAnswer({question,slug,title,context},{onDelta:appendDelta, onDone:finishStreaming, onError:failStreaming+提示})`。`App` 底部接入 `.app-ask`（QuestionConfirm + AskBar），空态/加载提示改为**仅在未打开主题时**显示（避免提问后与笔记区重叠）。**浏览器实测**（uvicorn :8000 + `npm run dev` :5173，真实调用大模型）：① 空态输入「什么是 Docker 容器？」→「优化」→ 出现确认卡（「新建《Docker 容器》」+ slug `docker`，问题可编辑）；② 改题为「…请用生活化的类比解释。」→「确认并作答」→ `.note-view` 由 205 字**流式增长到 2051 字**并渲染出 `h1`；③ 抓包确认 `POST /api/answer` 请求体为**改后**的 `question` + `slug`/`title`；④ 注入引用片段后**空问题**点「优化」→ 抓包确认 `POST /api/optimize/initial` 的 `question` = 「请详细讲解这段内容」且 `context` 与注入文本一致；⑤ 控制台无报错。验证产物（临时 `docker` 笔记文件 + `notes`/`questions` 行、`main.tsx` 临时注入钩子、临时脚本、两个服务）已清理。

- [x] **T2.6 流式打字机（笔记区底部实时渲染 + done 覆盖校正）**
  - 做：`delta` 直接在**笔记区底部**累积渲染出当前新 Q/A 块；`done` 用后端 `note_markdown` **覆盖校正**；`streaming` 期间禁止标注。
  - 产出：流式渲染逻辑接入 `NoteView` + `store`（另加 `NoteView.css` 流式样式、`QuestionConfirm` 改为 `beginStreaming(question)`、Plan §8 同步 `streamingQuestion`/`streamingText` 说明）。
  - 验证：浏览器提问后，回答实时流入笔记区，结束后被校正一次。
  - 实测：`npm run build`（`tsc -b && vite build`）→ 通过（226 modules）；`npm run lint` → 无告警。实现要点：`store` 新增 **`streamingQuestion`/`streamingText`** 两字段——`appendDelta` 只累加到 `streamingText`（**不写入 `noteMarkdown`**，避免每个 delta 重解析整篇笔记），`beginStreaming(question)` 记下新块标题并清空缓冲，`finishStreaming(note_markdown)` 用后端全文**覆盖** `noteMarkdown` 并清空两字段、`failStreaming` 丢弃缓冲；`NoteView` 在原 `noteMarkdown` 之下、**流式期间**额外渲染 `<section class="note-streaming">`（状态文字「正在作答…（暂不可标注）」+ `<h2>{问题}</h2>` + `streamingText` 的 Markdown + 闪烁光标 `.note-cursor`），并以 `bottomRef.scrollIntoView` 自动滚底；`streaming` 时给 `.note-view` 加 `--streaming` 类（`user-select: none`）禁用划词。**浏览器实测**（uvicorn :8000 + `npm run dev` :5173，真实调用大模型）：① 空态「什么是 Docker 镜像与容器的区别」→ 优化 → 确认卡「新建《Docker 入门》」→ 确认并作答；② 确认后 1s 内即出现 `.note-streaming` 区块，含状态文字、`<h2>` 问题标题与 `.note-cursor`，正文**逐字增长**（多次连拍可见）；③ 流式进行中 `browser_evaluate` 测得 `.note-view` className = `note-view note-view--streaming`、`getComputedStyle(...).userSelect = none`、`.note-streaming`/`.note-cursor` 均存在（**流式期间禁标注生效**）；④ 流式结束后区块消失、类名恢复 `note-view`、`userSelect=auto`；⑤ 落盘笔记 `# Docker 入门` + `## 问题` + 完整正文，即 `done` **覆盖校正**成功（后端日志 `POST /api/answer 200 OK`）。验证产物（`data/notes/docker.md` + `notes`/`questions` 行、两个 dev 服务）已清理。

---

## M3 标注 + 问题确认 + 深度

**验证标准（Plan §10）**：标注后生成候选问题、确认深度、笔记原地扩写。

- [x] **T3.1 模式②扩写智能体 + Prompt**
  - 做：`prompts.py` 落 Plan §6.2 模板；`answer_note.py` 加模式②方法：输入 `note_markdown`/`anchor`/`question`/`kind`/`concept`/`depth` + 背景与两类概念 → 输出 JSON `{insert:{insert_text}, new_section?}`（`brief` 无 `new_section`）。
  - 产出：模式② Prompt 与方法（另加 `tests/test_annotate.py`）。
  - 验证：`brief`/`deep` 各调一次，返回结构符合契约且解析成功。
  - 实测：`uv run pytest -q` → `90 passed`（新增 6 个 `test_annotate` 用例）。`prompts.py` 新增 `ANNOTATE_TEMPLATE`（逐字对齐 Plan §6.2）与 `render_annotate_prompt`，并新增 `QUESTION_NA`（=「（未提供，仅凭片段与上下文）」）与 `_or_question_na`——模式② 的 `question` **可选**，为空时按 Plan §6 渲染约定走该专用默认（其余占位符空值仍「（暂无）」）；`answer_note.py` 加 `annotate()`（组装 Prompt → `chat_json(ANNOTATE_SYSTEM_PROMPT, user, AnnotateResult)`，复用 T1.8 的 JSON 约束与回喂重试），原模式① `SYSTEM_PROMPT` 更名 `ANSWER_SYSTEM_PROMPT` 以免同模块两模式歧义。**真实调用**（临时脚本，验证后已删）`annotate(...)`：`depth=brief` → `AnnotateResult`，`insert_text` 为一两句简要解释、`new_section is None`；`depth=deep` → `insert_text` + `new_section{title, content}`（title「容器到底是什么：从进程隔离到镜像运行实例」、content 为完整 Markdown 深讲），结构符合 §6.2 契约、解析成功。（本任务只做智能体与 Prompt；`/api/annotate` 路由与落盘在 **T3.3**。）

- [x] **T3.2 模式B 接口（标注 → 候选问题）**
  - 做：`prompts.py` 落 Plan §6.4 模板；`question_optimizer.py` 实现模式B 方法；`POST /api/optimize/annotation`：入参 `{snippets[], note_markdown}` → 出参 `{questions:[{text,concept,kind,reason}]}`（**一标注一问题、不合并**）；组装时注入 `{concepts_existing}`——读 `concepts` 表**仅名称清单**（Plan §6.4/§7），使模式B **优先复用已有概念名**。
  - 产出：模式B 路由 + Prompt + `app/agents/question_optimizer.py` 模式B 方法。
  - 验证：传多个 snippet，返回条数一致、每项字段齐全。
  - 实测：`uv run pytest -q` → `96 passed`（新增 6 个用例：`test_question_optimizer` 4 + `test_api` 2）。`prompts.py` 新增 `OPTIMIZE_ANNOTATION_TEMPLATE`（逐字对齐 Plan §6.4）+ `render_optimize_annotation_prompt`，并加两个渲染工具：`format_snippets`（`{snippets}` → **编号列表** `1. 片段`，每片段一行，便于一一对应）、`format_concept_names`（`{concepts_existing}` → **逗号分隔的仅名称清单** `容器, 镜像`，不含分数/等级）；空值统一「（暂无）」。`question_optimizer.py` 加 `optimize_annotation()`（组装 → `chat_json(OPTIMIZE_ANNOTATION_SYSTEM_PROMPT, user, OptimizeAnnotationResult)`，复用 T1.8 JSON 约束与回喂重试），原模式A `SYSTEM_PROMPT` 更名 `OPTIMIZE_INITIAL_SYSTEM_PROMPT` 以免同模块两模式歧义。`memory.py` 加 `list_concept_names()`（`SELECT name FROM concepts ORDER BY name`）；`api.py` 加 `OptimizeAnnotationRequest{snippets, note_markdown}` 与 `POST /api/optimize/annotation`（读 background + 仅名称概念清单 → 调模式B → 返回 `questions[].model_dump()`；`JsonParseError` → `502`）。**真实调用**（临时脚本，验证后已删）传 3 个 snippet（`容器` / `容器是轻量级的` / `镜像`），`concepts_existing=["容器","镜像","低耦合"]` → 返回 3 条（条数一致）、每项 `text/concept/kind/reason` 齐全，`kind` 正确区分 `concept`（术语）与 `statement`（判断句），`concept` 复用已有名「容器/镜像」，排序为先基础（容器/镜像）后深入（轻量级说法）。

- [x] **T3.3 标注落盘接口**
  - 做：`POST /api/annotate` 按 Plan §7：运维顺序 = 读笔记 → 模式② → 定位插入（`brief` 插简释 / `deep` 加 `####` 章节，其 `content` 行首标题压为 `###`）→ 写 `annotations`（`note_id`/`snippet`/`concept`/`depth`）与 `questions(source='annotation')` → 返回更新后 `note_markdown` → 刷新 `notes.updated_at`。**本里程碑先不写概念记忆**（Plan §7 步骤3 的 `concepts`/`pending` 写入由 **M4 T4.6** 接入；M3 验证标准只要求"笔记原地扩写"）。
  - 产出：`app/api.py` 路由。
  - 验证：curl 标注一次，笔记在锚点后出现解释（deep 另有 `####` 章节），`annotations`/`questions` 各增一行。
  - 实测：`uv run pytest -q` → `102 passed`（新增 6 个 `test_api` 用例：brief 落盘+索引、deep 加 `####` 且 content 压 `###`、空 question 退回 anchor、未知主题 404 不调 LLM、锚点定位失败 422 不写 annotations、两次非法 → 502）。`api.py` 加 `AnnotateRequest{slug, anchor, concept="", kind="concept", depth="brief", question=""}` 与 `POST /api/annotate`：校验主题存在（否则 404）→ 读笔记 → 组装背景+两类概念 → `answer_note.annotate()`（模式②，`JsonParseError` → 502）→ `notes.insert_annotation(...)`（`AnchorNotFoundError` → 422）→ 写 `memory.insert_annotation`（snippet/concept/depth）+ `insert_question(source='annotation'，text 取 question、空则退回 anchor)` → `touch_note` → 返回 `{note_markdown}`。`memory.py` 加 `insert_annotation()`。**回填 Plan §7 步骤4**：`questions.text` 空 `question` 时退回 `anchor`（本轮与用户确认的决策）。**真实调用**（TestClient + 真实 DeepSeek，独立 slug `t3-annotate-smoke`，验证后文件/DB 行均清理）：brief 传 anchor=`镜像` → 源码命中、**词后**插入 `<a id="anno-…-src">[镜像](#anno-…)` + 简释 + `[↩]` 双向跳转；deep 传 anchor=`容器` → 段末插入简释并紧跟 `#### 深入理解容器` 章节（content 内标题压为 `###`）；`annotations` 2 行、`questions(source='annotation')` 2 行。

- [x] **T3.4 划词标注（前端）**
  - 做：`NoteView` 在**流式结束且 MD 渲染稳定**后用 `window.getSelection()` 捕获选中文本，仅限**笔记区**，然后**按字符数分流**（Spec FR-4a/FR-4b）：**去首尾空白 > 60 字** = 大块 → **不进标注列表**、移交**提问区**（带 `context`，走 T2.5 追问链路）；**≤ 60 字** → 写入 `annotations[]`（既有标注流程）。
  - 产出：划词逻辑 + 标注气泡/列表 + 大块分流。
  - 验证：流式期间无法划词；选短文本出现在标注列表；选**大块（>60 字）不出现在标注列表**、提问区出现该引用。
  - 实测：`npm run build`（`tsc -b && vite build`）→ 通过（228 modules）；`npm run lint` → 无告警。`NoteView` 在笔记区容器上挂 `onMouseUp`：先按 `useCanAnnotate`（`idle`/`done`）判定可否标注，再用 `window.getSelection()` 取选区并校验**起止点都落在 `.note-view` 内**，取 `sel.toString().trim()` 后**按字符数分流**——`> 60` → `setPendingContext(text)`（大块转追问，清空选区、不弹气泡）；`≤ 60` → 按选区 `getBoundingClientRect()` 在**选区上方浮出 `.note-bubble`「标注」**按钮（`position: fixed` + `transform: translate(-50%,-100%)`；`onMouseDown preventDefault` 防止点击清空选区），**点击才** `addAnnotation`；气泡在选区消失/点击空白/页面 `scroll`（capture）时自动关闭。新增 `AnnotationList.tsx`/`.css` 展示 `annotations[]`（标签 + 「×」移除），渲染在 **App 的 `.app-ask` 提问/确认区上方**（与引用片段同区）；`Plan §8` 的「划词标注」补一行**确认方式（v1）**记录气泡与列表位置。**浏览器实测**（Playwright + Chromium，uvicorn :8000 + `npm run dev` :5173，预置含长句的笔记；**13/13 PASS**）：① 程序化选中 `只读模板`（≤60 字）→ `.note-bubble` 出现 → **点击后** `.anno-item` 计数 1 且文本为 `只读模板`、气泡消失；② 选中 `容器（container）…虚拟化开销。`（>60 字）→ **不弹气泡**、`.anno-item` 计数不变、`.ask-context` 出现且**文本与选区一致**（去首尾空白）；③ 真实提问进入流式 → `.note-view` 计算样式 `user-select: none`、流式期间划词**不弹气泡也不进列表**，流式结束后 `.note-streaming` 消失（`done` 覆盖校正）。验证产生的种子笔记、模式A 新建的 `container-vs-image` 笔记及其 `notes`/`questions` 行、临时脚本（种子/清理/Playwright）均已删除，库回到 `notes=[]`、`concepts=[]`。

- [x] **T3.5 候选问题确认区（标注态）**
  - 做：划词结束后**整批一次性**调模式B，同一确认区切换为候选问题列表（可删减/编辑），每条选**简单/深入**；**下钻的新标注单独再走一遍模式B**并**插到队列最前**。
  - 产出：`QuestionConfirm.tsx` 标注态。
  - 验证：多标注并行展示；下钻标注优先排到队首。
  - 实测：`npm run build`（228 modules）+ `npm run lint` 均通过。**触发方式（与用户确认）**：标注收集区（`AnnotationList`）右侧加**手动「生成候选问题」按钮**（不做自动 debounce，避免每划一次词就调一次 LLM）→ 整批 `optimizeAnnotation({snippets, note_markdown})` → `setPendingQuestions` + `setAnnotationCard`（phase 走 Plan §8 的 `optimizing`）；候选卡展示期间标注收集区**隐藏**（同一区域按状态复用）。`QuestionConfirm` 新增**标注态**：每条候选问题**可编辑 `text`**、展示 `kind`（概念/说法）、`concept`、`reason`，**单选「简单了解/深入了解」**、可「删除」；`pendingQuestions` **全部选定深度**后才允许「开始扩写」（否则按钮禁用）→ `clearAnnotations()` + `beginAnnotating()` 进入 `annotating`；`annotating` 期间渲染**只读待处理队列**（深度未定显示「待选」）。**下钻**：`QuestionConfirm` 内 `useEffect` 监听 `phase==='annotating'` 下新增的 `annotations`，**立即单独** `POST /api/optimize/annotation` → `prependPendingQuestions` **插到队首**（用 `useRef` 记录已发送 id 防 StrictMode 双跑），完成后清空收集区；配套把 store 的 `useCanAnnotate` 扩为 `idle`/`done`/**`annotating`**（`annotating` 用于下钻，`streaming`/`optimizing` 仍禁标），`store` 新增 `prependPendingQuestions`。`Plan §8` 标注态一行补记触发时机与下钻规则。**浏览器实测**（Playwright + Chromium，uvicorn :8000 + `npm run dev` :5173，**真实调用模式B**；**15/15 PASS**）：① 划两条标注 → 点按钮 → 候选列表出现 **2** 条（与标注一一对应、字段齐全）；② 未选深度「开始扩写」禁用，仅选一条仍禁用，**两条都选后启用**；③ 候选问题**可编辑**（改后值回读一致）、**可删减**（2→1）；④ 「开始扩写」后进入 `annotating`、队列显示 1 条且首项为改后问题；⑤ `annotating` 期间在笔记区**再划「宿主机内核」** → 气泡出现（仍可划词）→ 点击后队列变 **2** 条、**下钻新问题排在队首**（原首项后移）、收集区已清空。验证产物（种子笔记、`notes`/`questions`/`annotations` 行、临时脚本）已删除，库回到 `notes=[]`、`concepts=[]`。

- [x] **T3.6 一次一个串行执行**
  - 做：确认一条 → `POST /api/annotate` → 刷新笔记 → 用户读 → 点「已理解」→ 下一条（或点「跳过」→ **前端纯本地动作**：不调 `confirm`、记忆不变、不回滚扩写，直接下一条）；**单条失败即中断**，前端提示**该条重试**（不推进队列）；每次以最新 `note_markdown` 更新状态。（「已理解」按钮调 `POST /api/memory/confirm` 的接线见 **M4 T4.6**。）
  - 产出：串行执行逻辑 + 失败提示。
  - 验证：故意让某条失败，队列停在该条、提示重试，后续条不执行；点「跳过」后队列推进、该概念分数不变。
  - 实测：`npm run build`（228 modules）+ `npm run lint` 均通过。**串行模型（Plan §8）**：`QuestionConfirm` 以组件本地状态 `currentId` 追踪**当前唯一执行条**（下钻插队**不改** `currentId`），`expanding`/`failed` 分别标记进行中与失败；`runExpand(q)` 调 `POST /api/annotate`（`anchor` 取该条 `snippet`）→ 成功用返回的**最新 `note_markdown`** `openNote` 覆盖、显示「已理解/跳过」；失败置 `failed`、展示错误 + 「重试该条」，**不推进队列**。「已理解」与「跳过」在 T3.6 均为**前端本地动作**（共用 `advance()`：`removePendingQuestion(currentId)` → 下一条 `startItem`，**队首优先**；无则 `finishAnnotating` 回 `idle`），`已理解` → `POST /api/memory/confirm` 的接线留待 **M4 T4.6**；下钻项（`depth=null`）轮到它时先让用户选「简单/深入」再扩写。「开始扩写」`onClick` 改调 `handleStart()`。**契约补全（先改 Plan §8 再改码）**：模式B 输出 `questions` 与输入 `snippets` **按下标一一对应**，前端把划词原文并入候选项新增字段 `snippet`（**前端专有、非后端契约**），作 `/api/annotate` 的 `anchor`；`store` 的 `PendingQuestion` 加 `snippet`、`setPendingQuestions`/`prependPendingQuestions` 改收 `CandidateWithSnippet`，`AnnotationList` 与下钻 effect 处按 `snippets[i] ?? q.concept` 兜底 zip。**浏览器实测**（Playwright + Chromium，uvicorn :8000 + `npm run dev` :5173，`page.route` 令**第 1 次** `/api/annotate` 返回 500；**20/20 PASS**）：① 划两处短词 → 生成候选 2 条 → 全选「简单了解」→「开始扩写」；② 首条 500 → 显示 `.serial-failed` + 「重试该条」、**无「已理解/跳过」**、错误提示含 `boom`、**annotate 仅 1 次**、队列仍 2 条（**失败中断、未推进**）；③ 点「重试该条」→ 真实成功、出现「已理解/跳过」、annotate 累计 2 次、笔记内出现标注锚点 `anno-`；④ 点「跳过」→ 队列递减为 1、当前条切换、第 2 条 annotate（累计 3 次）成功；⑤ 点「已理解」→ 队列清空、`annotating` 卡消失（回 `idle`）；⑥ **未调用** `/api/memory/confirm`、`concepts` 表**无写入**（分数不变）。验证产物（种子笔记、`notes`/`questions`/`annotations` 行、临时脚本）已删除，库回到 `notes=[]`、`concepts=[]`。

  - 缺陷修补（用户在真实使用中反馈：「系统让我选择已理解还是跳过时，我都看不到在哪里有解释和介绍」）：
    - 问题 1（F1，看不到解释在哪）：`POST /api/annotate` 只回 `note_markdown`，前端覆盖后**不滚动、不高亮**，且 brief 简释**未加括号**混在正文里无法分辨。
    - 问题 2（F2，解释插错位置）：模式B 输出无片段标识，前端按**位置** zip；模型按「学习顺序」重排后即**配错 anchor**（实测 `client-vs-java-backend.md` 第 48 行误插 SLA 解释、第 59 行误插灰度发布解释）。
    - 修法（**先改 Plan 再改码**）：① Plan §4.1 契约补 `/api/annotate` 响应带 `anchor_id`；§6.2 增「`insert_text` 必须整体用**全角括号（）**包裹」；§6.4 输出 schema 与任务补 `snippet_index`；§8 补「成功按 `anchor_id` 自动滚动并短暂高亮」「按 `snippet_index` 取原文、不依赖输出顺序」。② 后端：`CandidateQuestion` 加关键字段 `snippet_index`（缺失触发回喂重试）、`chat_json` 加可选 `validate` 钩子、`optimize_annotation` 校验下标**恰好覆盖 1..N**；`notes.insert_annotation` 返回 `(全文, anchor_id)`、`apply_annotation` 支持传入固定 `anno_id`、`api.py` 回带 `anchor_id`。③ 前端：`store` 加 `flashAnchorId`/`flashAnchor`；`AnnotationList`/下钻 effect 改按 `q.snippet_index - 1` 取原文配对；`QuestionConfirm.runExpand` 成功后 `flashAnchor(resp.anchor_id)`；`NoteView` 监听它 → `scrollIntoView({block:'center'})` + 给所在段落加 `.anno-flash`（1800ms 后移除）。
    - 实测：后端 `uv run pytest -q` → **108 passed**（新增/改：`CandidateQuestion` 缺 `snippet_index` 报错、`chat_json` 走 `validate` 失败回喂重试、下标重排可通过、两次仍不覆盖 1..N 抛错、`apply_annotation` 固定 `anno_id`、`insert_annotation` 返回 `anchor_id` 且 API 响应带 `anchor_id`）；前端 `npm run build` + `npm run lint` 全绿。**端到端实测**（Playwright + Chromium；`page.route` 令模式B 返回**故意重排**的 `snippet_index`，真实调 `/api/annotate`；**16/16 PASS**）：候选顺序按 index 还原为 `[服务等级协议, 灰度发布]`（配对不依赖输出顺序）→ 第 1 条 `anchor` = 「服务等级协议」、第 2 条 = 「灰度发布」（**配对正确**）；扩写成功后出现 `.anno-flash`、其段落落在视口内（`top=397/bottom=481`，视口 800）、`.app-main` `scrollTop 0 → 558`（**自动滚动定位**）、段落文本含全角括号「（」（**解释可辨认**）。验证产物（`verif-f2f1` 种子笔记 + 临时脚本）已清理。

- [ ] **T3.7 主题列表 + 手动切换 / 新建入口（插队，优先于 M4）**
  - 做：前端新增**主题列表** UI（读 `GET /api/notes`，按 `updated_at` 倒序），点击可在**已有主题间手动切换**（复用 `GET /api/note/{slug}` + `openNote`）；并提供**显式「新建笔记」入口**，不再只依赖模式A 自动判定新主题。
  - 前置（**先改 Plan §8 再改码**）：补主题列表的契约与交互——列表位置、空态、与「启动自动打开最近一篇」的关系（列表存在时是否仍自动打开）、新建入口如何与模式A 协同（显式新建时是否仍走 `/api/optimize/initial` 确认 slug/title）。后端若无需新接口则复用现有读接口。
  - 产出：主题列表组件 + 切换/新建接线（+ Plan §8 契约补充）。
  - 验证：能在多个已有主题间切换查看，各自笔记独立渲染；能显式新建一篇并正确落盘。
  - 背景：用户反馈**无法验证上一轮 bug 修复合集**——缺少打开**指定笔记**的入口，只能看自动打开的最近一篇；该项插队到当前任务，做完即可自行验收标注配对与滚动高亮。

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
- [ ] 选中一大块（> 60 字）→ 自动进提问区并带引用 → 追问 → 回答作为新 Q/A 区块追加到笔记末尾（不入标注列表、不改记忆）。
- [ ] 标注后：点笔记里的**原词**跳到其解释、点解释的「↩」跳回原词（通用 MD 阅读器打开亦可点）。
- [ ] 二次会话背景推断生效，且回答深度随掌握度变化。