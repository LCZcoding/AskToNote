# Plan.md — 提问式学习智能体（AskToNote）

> 状态：已定稿（v1 demo）
> SDD 第二步——技术实现方案，依据 [Spec.md](./Spec.md)。
> 原则：不推翻 Spec；数据模型/API/Prompt 在此细化。
> 文档集说明：本项目 SDD 采用精简三层（Spec → Plan → Tasks）；本文**兼作技术设计文档（Design）**——目录结构/数据模型/接口契约/Prompt/编排/前端交互/测试策略均在此落地，故不单独另设 Design.md。

---

## 1. 技术栈（回顾 Spec §9）

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13 + FastAPI + Uvicorn（uv 管理）；回答用 `StreamingResponse` 推 **SSE** |
| LLM | DeepSeek，**OpenAI 官方 SDK**（`base_url` 指向 DeepSeek），抽象层可换模型，支持流式 |
| 存储 | SQLite（标准库 `sqlite3`，不引入 ORM） |
| 笔记文件 | `data/notes/{slug}.md`（相对**运行目录 `backend/`**，即 `backend/data/notes/{slug}.md`） |
| 配置 | `.env`（需 `python-dotenv` 读取） |
| 前端 | Vite + React + TypeScript + zustand；react-markdown 渲染；dev 用 proxy 转发 `/api` |
| 测试 | 后端 pytest；前端 demo 阶段暂不引自动化测试 |

## 2. 目录结构

```
AskToNote/
├── docs/
│   ├── Spec.md
│   ├── Plan.md
│   └── Tasks.md
├── backend/
│   ├── main.py                 # FastAPI 入口（挂载路由 + CORS）
│   ├── app/
│   │   ├── config.py           # 读 .env：API key、模型名、路径
│   │   ├── api.py              # 路由层（薄，只做参数校验与编排）
│   │   ├── llm.py              # LLM 抽象层（chat / chat_stream + DeepSeek 实现）
│   │   ├── prompts.py          # Prompt 模板常量
│   │   ├── parse.py            # JSON 输出约束：Pydantic 模型 + 解析容错 + 回喂重试（§7）
│   │   ├── agents/
│   │   │   ├── answer_note.py       # 解答笔记智能体（模式①作答 / ②标注扩写）
│   │   │   └── question_optimizer.py # 问题优化智能体（模式A / 模式B）
│   │   ├── memory.py           # SQLite 读写 + 掌握度规则
│   │   └── notes.py            # MD 读写 + 段落定位插入
│   ├── tests/
│   │   ├── test_notes.py       # 段落定位插入
│   │   ├── test_memory.py      # 掌握度规则
│   │   └── test_parse.py       # JSON 解析容错
│   ├── data/
│   │   ├── asktonote.db
│   │   └── notes/
│   ├── .env.example
│   └── pyproject.toml
└── frontend/
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx
    │   ├── store.ts            # zustand：会话/标注/问题/笔记/streaming
    │   ├── api.ts              # fetch + SSE 封装
    │   ├── types.ts            # 与后端 JSON 契约对齐
    │   ├── remark-wikilink.ts  # [[...]] 内链插件（轻量高亮渲染）
    │   ├── remark-highlight.ts # ==...== 强调高亮插件（Typora/Obsidian 通用语法）
    │   └── components/
    │       ├── NoteView.tsx        # 笔记区（MD 渲染 + 流式追加 + 划词标注）
    │       ├── AskBar.tsx          # 提问框（输入原始问题 +「优化」）
    │       ├── QuestionConfirm.tsx # 问题确认区：模式A（问题可改/主题只读）与模式B（候选问题+深度）复用
    │       └── SettingsPanel.tsx   # 背景填写
    ├── package.json
    └── vite.config.ts
```

## 3. 数据模型（SQLite DDL）

> 单用户，`user_profile` 固定一行（id=1）。**v2 多用户扩展方式**：各表加 `user_id` 列、读写带 user 条件（v1 不加列）。

```sql
CREATE TABLE concepts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,   -- 规范概念名（去重；来自模式B 的 concept）
    score        INTEGER NOT NULL DEFAULT 10,  -- 0-100，等级由其推导；被标注不改分、确认 +10
    pending      INTEGER NOT NULL DEFAULT 0,   -- 1=已被标注、等待「已理解」确认（兼作确认幂等依据）
    updated_at   TEXT NOT NULL
);
-- 等级由 score 推导：<20 unfamiliar | 20-49 blurry | 50-79 familiar | ≥80 mastered

CREATE TABLE user_profile (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    background        TEXT NOT NULL DEFAULT '',   -- 初始空串
    background_locked INTEGER NOT NULL DEFAULT 0  -- 1=用户手改过，停止被自动推断覆盖
);

CREATE TABLE notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    file_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 问题索引：问题正文同时写入笔记的 Q/A 区块；"实际被问"时落盘（initial 在 /api/answer、annotation 在 /api/annotate）
CREATE TABLE questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER REFERENCES notes(id),
    text        TEXT NOT NULL,
    concept     TEXT,                   -- 该问题针对的规范概念（source='initial' 时为 NULL）
    source      TEXT NOT NULL,          -- 'initial' | 'annotation'
    created_at  TEXT NOT NULL
);

CREATE TABLE annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER REFERENCES notes(id),
    snippet     TEXT NOT NULL,          -- 用户划词的原文（锚点）
    concept     TEXT,                   -- 该标注对应的规范概念名（模式B 给出）
    depth       TEXT NOT NULL,          -- 'brief' | 'deep'
    created_at  TEXT NOT NULL
);
```

## 4. API 契约

### 4.0 字段命名对照表

| 字段 | 含义 |
|---|---|
| `slug` / `title` | 主题的英文唯一短名 / 中文显示名（**AI 生成、用户不可改**；后端把 `slug` 清洗为合法字符并保证与已有主题不重复） |
| `new_topic`（请求参数） | **是否显式新建主题**：`true` 时模式A 另命名 `slug`/`title`（用户点「新建笔记」或在无主题时首次提问）；默认 `false`（并入当前主题） |
| `optimized_question` | 优化后的问题 |
| `questions` | 候选问题列表 `[{text, concept, kind, reason}]` |
| `reason` | 候选问题的猜问理由 |
| `anchor` | 用户划词的原文，用于定位**插入点**（grep 命中源码 → 插其之后；跨行内标记时退化为所在段落末尾；重复时取首次出现） |
| `insert` / `insert_text` | 模式② 输出：`insert` 为对象 `{insert_text}`；`insert_text` 是要插入的括号解释正文（**插入位置用请求里的 `anchor` 定位**，不用 LLM 回显） |
| `new_section` | 深入讲解时新增的小标题章节 `{title, content}`（标题渲染为 `####`） |
| `depth` | 了解深度：`brief`(简单) / `deep`(深入) |
| `concept` | 规范化概念名 |
| `concepts[]` | **`GET /api/memory`（只读）**返回的概念列表，元素 `{name, score, level}`；`level` 由后端按 §3 阈值推导（**单一真相源**，前端不再自算），分数**不可手改**；`pending`（**兼作「已理解」幂等依据**）为**内部字段、不返回前端** |
| `concepts_mastered` / `concepts_learning` | Prompt 组装时按 `score` 切分：`≥ 50` → 【已掌握概念】；`< 50` → 【尚不熟悉的概念】（见 §6 渲染约定） |
| `kind` | 标注对象类型：`concept`(概念) / `statement`(说法) |
| `snippets` | 用户划词选中的原文片段（可多个） |
| `context` | **引用片段**：大块（>60 字）追问时携带的选中原文（**可选字段**）；**只进 Prompt（【引用片段】）、不进 `## 问题` 标题**（Spec FR-4b） |
| `note_markdown` | 笔记全文（Markdown 源码） |
| `background` | 用户背景描述 |
| `delta` / `done` / `error` | SSE 事件类型（`type` 字段）：正文增量 / 流结束 / 出错收尾（`error` 带 `message`） |
| `source` | 问题来源（`initial` / `annotation`） |

### 4.1 接口

| 方法/路径 | 请求 | 响应 |
|---|---|---|
| `POST /api/optimize/initial` | `{ question, new_topic?, context? }` | `{ optimized_question }`；**`new_topic=true` 时**另含 `{ slug, title }` |
| `POST /api/optimize/annotation` | `{ snippets[], note_markdown }` | `{ questions:[{snippet_index, text, concept, kind, reason}] }` |
| `POST /api/answer` | `{ question, slug, title, context? }` | **SSE**：`delta`{text} 多次 → `done`{ note_markdown } |
| `POST /api/annotate` | `{ slug, anchor, concept, kind, depth, question? }` | `{ note_markdown, anchor_id }` |
| `GET /api/note/{slug}` | - | `{ slug, title, markdown }` |
| `GET /api/memory` | - | `{ background, concepts:[{name, score, level}] }` |
| `POST /api/memory` | `{ background? }` | `{ ok }` |
| `POST /api/memory/confirm` | `{ concept }` | `{ ok, score }` |
| `POST /api/memory/refresh` | - | `{ background }` |
| `GET /api/notes` | - | `[{ slug, title }]`（**按 `updated_at` 倒序**，最新在前） |

**`/api/answer` 的 SSE 线格式**：每条事件为一行 `data: {JSON}\n\n`（**不使用 `event:` 名**，事件类型放在 JSON 的 `type` 字段里）：

```
data: {"type":"delta","text":"流式增量文本"}\n\n
data: {"type":"delta","text":"..."}\n\n
data: {"type":"done","note_markdown":"落盘后的笔记全文"}\n\n
```

出错时用 `data: {"type":"error","message":"..."}\n\n` 替代 `done` 收尾。前端按 `type` 分发（`delta` 累积渲染、`done` 刷新笔记、`error` 提示）。

**`/api/answer` 说明**：`slug`/`title` 由**前端**决定——**并入当前主题**时用当前主题的 `slug`/`title`（`slug` 取当前打开笔记）；**显式新建主题**时用模式A 命名、并经后端**清洗+去重**后的 `slug`/`title`。`slug` 已存在 → `title` **以 `notes` 表为准（忽略请求值）**，以 **Q/A 区块**（`## {确认后的问题}` + 回答正文）**追加**到该主题笔记末尾；新 `slug` → 新建文件（先写 `# {title}`）并**新建** `notes` 记录。落盘在流结束后进行；两种写入**均刷新 `notes.updated_at`**（新建时同时写 `created_at`）。写入笔记的问题用**用户确认后的优化问题**。

**`context` 说明（模式A 与 `/api/answer` 通用，Spec FR-4a/FR-4b）**：**可选字段**，仅当用户以**大块选区**（去首尾空白 > 60 字）发起**追问**时携带。后端把它渲染为 Prompt 的【引用片段】：`/api/optimize/initial` 用它辅助**问题优化（新建主题时也辅助命名）**，`/api/answer` 用它辅助**作答**；**不进 `## 问题` 标题**（标题 = 用户确认后的问题；问题留空时前端填默认文案「请详细讲解这段内容」）。普通提问不传该字段（渲染为「（暂无）」）。

**`/api/annotate` 说明**：定位由后端工具函数完成（**优先词后、兜底行末**；**不做**"全局脱标记→映射回源码坐标"）：
1. 按空行把源码切成**段落**（每段保留源码与其位置）；
2. **grep 优先**：逐段在**源码**里 `find(anchor)`，命中**正文段** → 记录该段内 anchor 的**结束位置**（用于**词后**插入）；
3. **脱标记兜底**：源码搜不到（anchor 跨行内标记，如 `**容器**和**镜像**`）→ 对每行**单独**脱去行内标记（`**`、`_`、`[]()`、行内反引号、行首 `#`）后 `find(anchor)`，命中 → 退化为**行末**插入；
4. 命中仅在**标题行**时**只把简释的插入点**改取该标题下**首个正文段**（其首行行末）——原词仍**在该标题行内**（源码精确），故照常参与 FR-15 高亮升级与 FR-9c 双向跳转（T3.14）；都找不到 → 报错，前端提示重选；
5. **插入点界定**：**词后** = 源码精确命中原词时紧跟原词之后；**行末** = 锚点所在**那一行**末尾（列表项 `- `/`1. `、表格行等各自成行时以**该行行末**为准——避免降级命中时把解释插到整个列表/表格段的末尾，如 `**RESTful API**` 应落在它所属的那一条列表项行末）；**段末** = 该空行分隔段末尾（同一段落内多行时使用）。
6. 在插入点插入 `insert_text`：**先把原词就地升级为强调高亮** `==原词==`（Spec FR-15，见下「名词高亮升级」）；`brief` 时在其**前**写解释侧锚点 `<a id="anno-{n}"></a>`（即 `==原词==<a id="anno-{n}"></a>（简释）`）、**不写链接与回跳**；`depth="deep"` 时作为原地旁注（**不带锚点与回跳**）；`depth="deep"` 时把 `new_section` 作为新小标题章节（**标题固定 `####`**）**追加到笔记文件最末尾**（其 `content` 行首标题同样**压为 `###`**，**代码围栏内除外**），并在该章节末尾追加回跳链接 `[↩](#anno-{n}-src)`。

更新后刷新 `notes.updated_at`。**响应另带 `anchor_id`**（= 本次解释侧锚点 id `anno-{n}`），供前端扩写成功后**滚动定位并短暂高亮**该解释（§8）。**前端一次只提交一个标注**：本条扩写返回、用户读后点「已理解」（`POST /api/memory/confirm`）才提交下一条；用户也可点「跳过」（**前端纯本地动作**：不调 `confirm`、记忆不变、不回滚扩写）直接进入下一条；**单条失败即中断**（后续标注不再处理，前端提示失败的那条重新选择），保证序列一致；每次以后端返回的最新 `note_markdown` 更新前端状态（后端每次重读磁盘最新笔记，避免并发写互相覆盖）。**递归下钻**：用户可在新扩写内容上继续划词，新标注立即 `POST /api/optimize/annotation` 拿候选问题与深度，并**插到待处理队列最前**优先处理（后序：先弄懂叶子再回上一层）。

**标注跳转标记（Spec FR-9c，写入笔记源码）**：后端在定位插入时，为**深入了解**的标注分配**短 id** `anno-{n}`（`n = uuid4().hex[:8]`）并写入锚点，使**原词 ⇄ 文末深释章节**在通用 MD 阅读器里**双向可点**；**「简单了解」只写解释侧锚点、不做任何跳转**（简释就插在原词旁，任何方向的跳转都是空跳，白留死锚点）：
- **名词高亮升级（Spec FR-15，`brief`/`deep` 均做）**：**源码精确**命中原词（`"source"` 正文段命中，或 `"heading"` **仅命中标题行**——原词就在**标题行内**、属源码精确，**照常升级**）、且它**不在无法安全嵌套的标记内**时，把原词**就地重建为强调高亮** `==原词==`——原为行内代码 `` `原词` ``（模式① 首次提及的名词）、裸文本、或**同主题** `[[原词]]` 时替换其包裹标记为 `==…==`；**已经是 `==原词==`** 则原样保留（不重复包）；落在 `[...](...)`、`**...**`、**跨主题** `[[slug#概念]]` 内时**不重建**（走下方「降级」）。**标题行命中时升级位置与简释插入点分离**：原词在标题行内升级/包链接，简释仍插到标题下**首个正文段首行行末**（T3.14）；实现上按升级前后**长度差平移**插入点。
- **原词链接（`deep`）**：`deep` 时在**升级后的高亮**外再包成 Markdown 链接 `[==原词==](#anno-{n})`，并在词前插来源锚点 `<a id="anno-{n}-src"></a>`，使原词可点击跳文末深释；
- **原地简释**：`brief` 写 `==原词==<a id="anno-{n}"></a>（简释）`——锚点供前端滚动定位高亮，**无链接、无来源锚点、无「↩」**，例：`容器是一种==轻量化==<a id="anno-3"></a>（即共享宿主内核）的技术`；`deep` 写 `[==原词==](#anno-{n})（简释）`（锚点在文末章节里）；
- **深释章节（`deep`）**：把 `<a id="anno-{n}"></a>` + `#### {title}` + `content` + `[↩](#anno-{n}-src)` **追加到笔记文件最末尾**——原词链接跳到底部详细章节、底部「↩」跳回原词；
- **降级（单向）**：仅脱标记兜底命中、或原词本身已在 `[...](...)`/`**...**`/跨主题 `[[slug#概念]]` 等无法安全嵌套的标记内 → **不重建高亮、不包原词、不加来源锚点与回跳**（文末章节同样不带「↩」）；解释插到**原词所在行末**（避免切断原词所在的行内标记，如 `**原词**`/`[[slug#原词]]`；用**行末**而非段末，可保证列表项/表格行各自成行时解释紧邻原词）。

## 5. LLM 抽象层（OpenAI SDK）

```python
# app/llm.py
from typing import Iterator, Protocol
from openai import OpenAI

class LLMClient(Protocol):
    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...
    def chat_stream(self, system: str, user: str) -> Iterator[str]: ...

class DeepSeekClient(LLMClient):
    def __init__(self, api_key: str, model: str, base_url: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        )
        return resp.choices[0].message.content

    def chat_stream(self, system: str, user: str) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self.model, stream=True,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
```

配置（`.env`）驱动：

```
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL=deepseek-flash
```

当前**所有调用统一使用 `MODEL`**（flash 级）；抽象层保留"按智能体分别配置模型"的能力，未来若要给某个智能体换更强的模型，只改配置。

**流式说明**：只有「模式①作答」走 `chat_stream`，输出的**纯 Markdown 直接作为 `delta` 事件推给前端**，不做任何结构化包裹、无分隔符解析。其余调用（优化、扩写、背景推断）走 `chat(json_mode=True)` 返回 JSON（输出约束与校验见 §7「JSON 输出约束」）。

## 6. Prompt 模板（正文）

> **占位符渲染约定**：`{topics}` → `"slug: title"`（如 `docker: Docker 入门`；**模式①作答 / 背景推断 / 模式A「命名版」**使用——模式A**优化版不含** `{topics}`/`{slug}`）；`{concepts_mastered}` → `score ≥ 50` 的概念，`名称(等级)`（如 `容器(familiar)`）；`{concepts_learning}` → `score < 50` 的概念，`名称(等级)`（如 `镜像(unfamiliar)`）。**概念记忆一律按 `score` 分这两类喂给 AI**——`< 50` 的多为用户标注过、尚未学会的词，**不得当作已掌握**。`{concepts_existing}`（**仅模式B 使用**）→ **全部概念名清单、不含分数/等级**（如 `容器, 镜像, 低耦合`），供模式B **优先复用同名概念**；`{snippets}`（**仅模式B 使用**）→ **编号列表**（`1. 片段`，每片段一行，便于与"一标注一问题"按序对应）；`{question}`（**模式①/模式A/模式②** 均用）为空时渲染为「（未提供，仅凭片段与上下文）」（模式①/模式A 的入参 `question` 为必填，实际不空）；`{context}`（**模式①/模式A** 用）→ **引用片段**（大块追问携带的选中原文，Spec FR-4b），普通提问为空、按下方空值约定渲染为「（暂无）」。**空值约定**：除 `{question}`（见上）外，其余占位符为空时**统一渲染为「（暂无）」**——典型：新主题首问的 `{note_markdown}`、首个会话的 `{topics}`/`{background}`/`{concepts_mastered}`/`{concepts_learning}`、模式B 的 `{concepts_existing}`。

### 6.1 解答笔记智能体 — 模式①作答（流式纯 Markdown）

```
你是一个学习助手，帮助用户在学习陌生领域的同时，把知识沉淀成结构化 Markdown 笔记。

【场景】用户正在学习主题「{title}」（slug={slug}）。
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50，多为用户标注过、尚未学会的词，如 "镜像(unfamiliar)"
【其他主题】{topics}   // 形如 "docker: Docker 入门"
【当前笔记】{note_markdown}
【用户问题】{question}
【引用片段】{context}

【要求】
1. 根据用户背景与掌握度决定讲解深度，必要时用其熟悉领域做类比；对【尚不熟悉的概念】要讲透，**不得当作用户已掌握**。
2. 直接输出 Markdown 格式的回答正文，不要包在代码块里，不要输出 JSON。
3. **名词高亮（Spec FR-15）**：
   - **首次提及的**本主题新概念用**行内代码**（反引号）包裹，如 `` `容器` ``、`` `console.log()` ``；
   - **已在【当前笔记】里出现过 `==名词==` 的**，说明该名词本笔记已解释过，**继续用 `==名词==`**（不要退回行内代码）。
4. 提及**属于其他主题**的概念时用 [[该主题slug#概念名]]，slug 只能取自【其他主题】清单。
5. 只输出**本次回答的正文**；问题标题由程序统一添加，你**不要**自己写 `#`/`##` 问题标题。
```

### 6.2 解答笔记智能体 — 模式②标注扩写

```
你是笔记扩写助手。用户在笔记中标注了陌生片段，请生成局部的解释内容。

【笔记全文】{note_markdown}
【用户标注的片段】{anchor}
【针对该片段的问题】{question}
【片段类型】{kind}   // concept=概念（词/术语）；statement=说法（一句话/判断）
【规范概念名】{concept}
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50，多为用户标注过、尚未学会的词
【了解深度】{depth}   // brief=简单了解；deep=深入了解

【要求】
1. `kind=concept` 给出简短解释；`kind=statement` 解释该**说法本身**，并可顺带点出相关概念 `{concept}`。
2. 按【用户背景】与掌握度决定讲解深度，必要时用其熟悉领域做类比；对【尚不熟悉的概念】要讲透。
3. 只输出 JSON：
{
  "insert": { "insert_text": "（简要解释，一两句话）" },
  "new_section": { "title": "小标题", "content": "深入讲解的 Markdown" }
}
   - depth=brief 时不要输出 new_section 字段。
   - depth=deep 时输出 new_section，程序会把它作为新小标题章节（**标题固定 `####`**）**追加到笔记文件最末尾**（不是紧跟锚点段落），并在其末尾加 `[↩]` 回跳；原地只保留 `insert_text` 括号简释。
   - `insert_text` **必须整体用全角括号（）包裹**、写成一句旁注（如 `（Docker 是一个容器化工具）`），**不得**写成独立句子或分点列表——它是插进正文的简释、要让读者一眼看出「这是标注解释」。
   - `insert_text` 的插入位置由后端统一决定（**优先词后、兜底行末**），你无需精确控制。
```

### 6.3 问题优化智能体 — 模式A（优化问题；显式新建时命名主题）

模式A 有**两种** user prompt。**模式A 永不判断"新建 vs 并入"**——主题归属完全由用户决定（默认并入当前主题；点「新建笔记」或在无主题时首次提问才新建）。

**（1）优化版（默认，`new_topic=false`）**：只优化问题，**不喂** topics、**不产出** slug/title。

```
你是提问优化助手。

【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）
【用户问题】{question}
【引用片段】{context}   // 用户选中追问的一大块原文，可空

【任务】把用户的问题优化得更清晰、更聚焦。
【输出】只输出 JSON：{"optimized_question": "..."}
```

**（2）命名版（`new_topic=true`）**：优化问题 + **命名新主题**（喂【现有主题】仅供 slug 避免重名）。

```
你是提问优化与主题命名助手。

【现有主题】{topics}   // 形如 "docker: Docker 入门"，仅供 slug 避免重名
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）
【用户问题】{question}
【引用片段】{context}

【任务】
1. 把用户的问题优化得更清晰、更聚焦。
2. 为这个问题**命名一个新主题**：给出英文短名 slug（仅小写字母/数字/连字符）与中文标题 title；
   slug **不得与【现有主题】中任何 slug 重复**。

【输出】只输出 JSON：{"optimized_question": "...", "slug": "...", "title": "..."}
```

### 6.4 问题优化智能体 — 模式B（标注 → 候选问题）

```
你是提问助手。用户在笔记中标注了一些陌生片段，请猜测针对每个片段用户真正想问什么。

【笔记全文】{note_markdown}
【标注的陌生片段】{snippets}
【用户背景】{background}
【已有概念】{concepts_existing}   // 全部概念名清单（仅名称，不含分数/等级）

【任务】
1. 针对每个片段，猜测用户最可能想了解的问题 text。
2. 给出该片段对应的**规范化概念名** concept（**优先复用【已有概念】中的名称**，确属新概念才新建；
   若片段是"说法"，提炼其中最值得了解的概念）。
3. 判断片段本身是「概念」（一个词/术语）还是「说法」（一句话/判断）→ kind。
4. **必须**在 snippet_index 里写出该片段在【标注的陌生片段】中的**编号**（1-based）：每个片段**各给一个问题**（**不合并**相近片段），编号须**恰好覆盖 1..N 各一次**。
5. 输出顺序可按学习顺序（先基础后深入）排列；**配对一律以 snippet_index 为准**，与输出顺序无关。

【输出】只输出 JSON：
{"questions": [ {"snippet_index": 1, "text": "...", "concept": "...", "kind": "concept", "reason": "..."} ]}
   // snippet_index：该片段在【标注的陌生片段】中的编号（1-based、覆盖 1..N）
   // kind ∈ "concept"(词/术语) | "statement"(说法/判断)
```

### 6.5 背景推断（会话级一次）

```
根据用户的概念掌握档案与已有学习主题，推断其熟悉或正在学习的领域，用一句话概括。

【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）
【已有主题】{topics}   // 形如 "docker: Docker 入门"（只给标题，不含笔记正文）

【要求】
1. 由【已掌握概念】推断**熟悉**的领域（如多个 Java 概念达 familiar/mastered → "熟悉 Java 后端"）；由【尚不熟悉的概念】**或【已有主题】**推断**正在学习**的领域（如已有 Docker 主题 → "正在学 Docker"）。
2. 无法明显推断则输出空字符串。

【输出】只输出 JSON：
{"background": "熟悉 Java 后端（可为空字符串）"}
```

## 7. 编排流程（后端 api.py）

```
POST /api/optimize/initial:（模式A，**不判断新旧主题**）
  new_topic=false（并入当前主题）：读 concepts(按 score 分【已掌握】/【尚不熟悉】)/background → 调**优化版** → 返回 {optimized_question}
  new_topic=true （显式新建主题）：读 主题清单/concepts/background → 调**命名版** → slug **清洗为 [a-z0-9-]** 并与已有主题**去重**（不符则回喂重试）→ 返回 {optimized_question, slug, title}

POST /api/answer (SSE):
  1. 校验 question/slug/title（slug/title 由前端决定：并入当前主题用当前值，显式新建用模式A 命名并经清洗去重后的值）
  2. 读 memory(concepts 按 score 分【已掌握】/【尚不熟悉】+background)、读 notes(当前主题 MD)、列出其他主题(slug/title) 供内链
  3. 调解答笔记智能体 chat_stream() → 逐块作为 delta 事件推送
  4. 流结束：**先清洗正文**——把正文中**行首标题（`#`/`##`/`###`…）一律压为 `###`**（`#`/`##` 由程序掌控，避免与 `## {问题}` 同级混淆；**代码围栏 ``` / ~~~ 内的行除外**，避免把代码里的 `#` 注释误压为标题）；若正文开头是重复的问题标题行则删除；整体被 ``` 代码围栏包裹则去围栏。再以 `## {问题}` + 清洗后正文 组成 Q/A 区块——slug 已存在 → 追加到文件末尾；新 slug → 新建文件（先写 `# {title}`）与 notes 记录；**均刷新 notes.updated_at**（新建时写 created_at）；写 questions(source='initial'，concept 置 NULL)；发 done

POST /api/optimize/annotation:（模式B）
  读 note_markdown + snippets + concepts(**仅名称**) → 调优化B → 返回 questions[]

POST /api/annotate:
  1. 读笔记 → 调解答笔记智能体(模式②) 得 {insert, new_section?}（`question` 可为空——为空时按 §6 渲染约定替换为「（未提供，仅凭片段与上下文）」）
  2. 定位插入点（**优先词后、兜底行末**）→ 插入 insert_text（`deep` 时也插，作为原地旁注）；`deep` 另把 new_section 章节（**章节标题固定 `####`**，其 `content` 的行首标题同样**压为 `###`**（**代码围栏内除外**））**追加到笔记文件末尾**、末尾加 `[↩]` 回跳
  3. 写 memory（**concept 与 kind 均来自请求**，由模式B 给出）+ 写 annotations（note_id/snippet/concept/depth）：概念**分数不变**；**不在库 → score=10**；两种情况**均 pending=1**（`kind=concept` 与 `kind=statement` 同规则）
  4. 返回更新后的 note_markdown；写 questions(source='annotation'，`text` 取请求 `question`、**为空时退回 `anchor`**，`concept` 取请求值)

GET /api/memory:
  读 user_profile + concepts（含等级）→ 返回 { background, concepts:[{name, score, level}] }

POST /api/memory:
  1. 若带 background → 写 user_profile.background 且置 background_locked=1（此后 refresh 不再覆盖）
  2. 返回 { ok }
  （概念分数**只读**、由规则计算，不经此接口修改）

POST /api/memory/confirm:
  1. 入参 concept
  2. **仅当该概念 pending=1** 时：pending=0、score=min(100, score+10)
     （pending=0 时**直接返回当前值、不改分** → 幂等，重复点击/重发无效）
  3. 返回 { ok, score }

POST /api/memory/refresh（**会话级一次**，前端启动时调）:
  读 concepts（含等级）+ 主题清单（仅标题）→ 调背景推断 → 若 background_locked=0 则写回 user_profile.background → 返回 { background }
```

**掌握度规则（v1，程序计算）**：分数制 0–100（等级由分数推导）——概念被标注**不改分**（**不在库 → score=10**）并置 `pending`（**同一概念多次标注仍只算一次确认**）；用户点「已理解」（**仅 `pending=1` 时生效，幂等**）→ `pending=0`、`score=min(100, score+10)`；`score≥80` 即 `mastered`。**概念分数只读、不可手改**。时间衰减留 v2。

**JSON 输出约束（三层，适用于模式A / 模式B / 模式② / 背景推断）**：
1. **API 层**：调用时带 `response_format={"type": "json_object"}`（`chat(json_mode=True)`，见 §5）——服务端保证输出是合法 JSON；Prompt 中本就有"只输出 JSON"字样，满足该模式对提示词的要求。
2. **校验层**：解析后按 **Pydantic 模型**校验字段与类型（schema 取自 §6 各模板；Pydantic 随 FastAPI 已在依赖中）。
3. **兜底层**：提取 JSON 一律用 **`extract_json`：取首个 `{` 到与它配对的 `}`**（**字符串感知**——扫描时跳过字符串内部的花括号，并识别 `\"` 转义）。**不得按 ``` 围栏裁剪**：模式② 的 `new_section.content` 本身就是 Markdown、常含 ```` ```python ```` 代码围栏，按围栏裁剪会把 JSON 截断在围栏处（T3.13）。解析失败或校验不过 → **把错误信息回喂、重试 1 次**；仍失败则返回错误、前端提示；非关键字段缺失按默认值兜底（见 §9 `test_parse`）。

> 模式①作答是纯 Markdown 流式，**不使用**本约束（否则破坏流式渲染）。

**双写说明**：笔记 MD 文件与 `notes` 表分别写入，demo 阶段不做事务回滚（失败时以文件为准）。

**DB 访问约定（v1）**：由 `memory.py` 统一封装——**每次操作开短连接、用完即关**；所有**写**操作经**进程内全局锁**（`threading.Lock`）**串行化**（保证任何两个写操作不会相撞，不会 `database is locked`）；初始化时设一次 `PRAGMA journal_mode=WAL`。读操作不加锁。理由：单用户下并发极少（用户阅读/标注很慢），此约定是最小代价的保险，避免 vibecoding 到处乱建连接。

**笔记文件写锁**：`notes.py` 内置一把模块级 `threading.Lock`，把"读文件 → 改 → 写文件"整个包成临界区（`/api/answer` 追加、`/api/annotate` 插入都走它）→ 写笔记串行化，避免两请求各读旧内容互相覆盖。锁**必须覆盖读+写两步**，不能只锁写。单用户低频，代价可忽略。

**接口实现约定（并发模型）**：所有后端接口一律用**同步 `def`** 定义（不用 `async def`）——本项目全链路是同步风格（OpenAI 同步 SDK、`sqlite3`、文件读写、`threading.Lock`），同步 `def` 由 FastAPI 自动放入**线程池**执行，等待 LLM / 抢锁只占用工作线程、**不阻塞事件循环**；若写成 `async def`，同步等待会直接卡住事件循环 → **整个服务假死**（单用户下线程池默认约 40 线程，容量充裕）。SSE 流式接口同理：`StreamingResponse` 配**同步生成器**（内部调 `chat_stream()` 并 `yield`），由 Starlette 自动放入线程池逐块取数推送。

**失败处理**：`/api/annotate` 单条失败即中断，**前端提示该条重试**（不推进队列），已成功写入的内容不回滚。

## 8. 前端关键交互

- **启动加载**：页面加载时调 `GET /api/notes`（**按 `updated_at` 倒序**）→ 有主题则**自动打开最近更新的一篇**（"继续上次学习"）；无主题则显示空态、引导提问。
- **页面结构（无独立「AI 回答区」）**：主区为**笔记区**，流式回答**直接追加进笔记**；侧边为**主题列表**（读 `GET /api/notes`，可切换已有主题、并有「新建笔记」入口）；另有**提问/问题确认区**（同一块区域按状态复用）与**设置页**。
- **一键导出（Spec FR-13b）**：笔记区**顶部工具条**左侧显示 `noteTitle`、右侧「导出 MD」按钮——**纯前端**：用 store 中的 `noteMarkdown` 造 `Blob`（`text/markdown;charset=utf-8`）→ `URL.createObjectURL` → 临时 `<a download>` 触发下载 → `revokeObjectURL`，**不新增后端接口**（原文已在 store，且与磁盘文件逐字一致，加接口属冗余）。文件名 = `noteTitle` 清洗 `\` `/` `:` `*` `?` `"` `<` `>` `|` 后的结果，**清洗后为空则回退 `{slug}.md`**；`streaming` 期间（笔记未定稿）或笔记为空时按钮**禁用**。
- **流式渲染**：`POST /api/answer` 用 `fetch` + `ReadableStream` 读 SSE（EventSource 不支持 POST）。收 `delta` 直接在**笔记区**累积渲染（当前主题新 Q/A 块实时长出）；收 `done` 用后端返回的 `note_markdown` 覆盖刷新笔记。
- **提问/问题确认区**：
  - 提问态：输入原始问题 → 点「优化」→ `POST /api/optimize/initial`（**并入当前主题**时 `new_topic=false`；**点过「新建笔记」或当前无主题**时 `new_topic=true`）→ **就地**展示确认卡（**优化后的问题可编辑**；主题只读——并入时显示 `并入《当前主题 title》`，新建时显示 `新建《AI title》` + `slug`，`slug` 不可改）→ 确认 → 发出。
  - 标注态：标注收集完（**用户点「生成候选问题」按钮手动触发**，避免每次划词都调 LLM）后，**整批标注一次性**调 `POST /api/optimize/annotation` → 同一区域切换为**候选问题列表**（每条可编辑/删减、单选**简单/深入**）→ 点「开始扩写」进入 `annotating`；**下钻**——`annotating` 期间在笔记区新标注的片段，**立即单独再走一遍模式B**（复用同一确认区）并把结果**插到队列最前、优先处理**（此时笔记区**仍可划词**；`optimizing` 拉取中与确认卡待处理时**不可标注**）。
- **划词标注**：在**笔记区**、流式结束后且 Markdown 渲染成稳定 DOM 时，用 `window.getSelection()` 捕获选中文本（后端按"**优先词后、兜底行末**"定位，见 §4.1），写入标注列表，符合 Spec FR-4。
  - **确认方式（v1）**：捕获到 ≤60 字的选区后，**在选区上方浮出小气泡「标注」**，用户**点击才写入** `annotations[]`（避免"选字复制"被误标，v1 无撤销）；气泡在点空白处、选区消失或**页面滚动**时自动关闭；已收集标注以**标签列表**展示在**提问/确认区上方**（每条可「×」移除），与引用片段同区。
- **划词分流（大块 → 追问）**：同一 `window.getSelection()` 取到的选中文本，**去除首尾空白**后按**字符数**分流——**> 60 字**视为**大块**，**不进标注列表**，自动把该文本作为**引用片段**带入**提问区**（无独立弹窗），用户输入问题（**可留空** → 默认「请详细讲解这段内容」），走既有提问链路（模式A → 确认 → `/api/answer`，请求带 `context`），回答**末尾追加**新 Q/A 区块；**≤ 60 字**走既有划词标注（Spec FR-4a/FR-4b）。
- **内链渲染**：`react-markdown` 外挂 `remark-wikilink` 小插件，把 `[[概念名]]` / `[[slug#概念]]` 转成高亮 `<span class="wikilink">`（v1 **不可点击跳转**；跳转/图谱留 v2）。
- **强调高亮渲染（Spec FR-15）**：`react-markdown` 外挂 `remark-highlight` 小插件，把 **`==名词==`** 转成 `<mark class="note-mark">`（Typora/Obsidian 同款高亮语法，配套 `.note-view .note-mark` 样式）。**链接文本**里的高亮——深入了解的 `[==原词==](#anno-{n})`——同样由该插件处理，故可点击链接里原词仍显示为高亮。行内代码（首次见的名词，如 `` `容器` ``）由 `react-markdown` 原生渲染为 `<code>`，无需插件。
- **GFM 渲染**：`react-markdown` 再外挂 **`remark-gfm`**（表格 / 删除线 / 任务列表 / 自动链接）——**表格不属于 CommonMark**，缺插件时回答里的 `| a | b |` 会退化成普通段落；配套在 `NoteView.css` 补 `.note-view table/th/td` 的边框与留白。
- **标注跳转渲染**：`NoteView` 的 `react-markdown` 开启 **`rehype-raw`**，以渲染标注写入的行内 HTML 锚点（`<a id="anno-{n}">`）与**深入了解**在文末的 `[↩]` 回跳链接；**原词 ⇄ 文末深释**的跳转由浏览器**原生锚点导航**完成（Spec FR-9c，**「简单了解」不写任何跳转**），**无需额外 JS**。
- **状态（zustand store）**：`annotations[]`、`pendingQuestions[]`、`noteSlug`/`noteTitle`/`noteMarkdown`（`noteTitle` 供「并入当前主题」时拼 `/api/answer` 的 `title`、并在确认卡只读展示）、`newTopicMode`（用户是否点了「新建笔记」：进入时**清空 `noteSlug`/`noteTitle`/`noteMarkdown` 让笔记区回到空白页**并显示「新笔记」引导与**「取消」按钮**——取消即退出新建态回到「未打开笔记」引导，再从侧边栏选一篇；供 AskBar 决定 `new_topic`；`openNote` 会复位它）、`optimizeCard`、`pendingContext`（大块追问的引用片段，Spec FR-4b）；另有流式专用的 `streamingQuestion`/`streamingText`——`delta` 只累加到 `streamingText`（**不写入 `noteMarkdown`**，避免每个 delta 重解析整篇笔记），在**笔记区底部**以 `## {streamingQuestion}` 为题实时长出「当前新 Q/A 块」；`done` 用后端 `note_markdown` 覆盖 `noteMarkdown` 并清空两字段。**"是否正在流式"由 `phase` 派生**（`useIsStreaming`），不单设布尔字段（避免"阶段"与"是否在流式"两份状态不同步）；`streaming` 期间给笔记区加 `--streaming` 类以 **`user-select: none` 禁止划词标注**。
- **状态流转**：`idle`（可提问/可标注）→ 提交提问 → `optimizing`（模式A 出确认卡）→ 确认 → `streaming`（**禁止标注**，回答流入笔记）→ `done`（可标注）→ 标注收集完 → `optimizing`（模式B 拉候选问题）→ 用户确认 → `annotating`（**一次一个**，用户点「已理解」或「跳过」后进入下一个；单条失败提示该条重试）→ 回到 `idle`。请求进行中禁用对应按钮，避免重复提交与状态错乱。
- **流程驱动**：标注完 → `POST /api/optimize/annotation` → QuestionConfirm（每项含深度选择「简单/深入」）→ 用户确认 → **逐个** `POST /api/annotate`，每条返回后刷新笔记；用户读后点「已理解」→ `POST /api/memory/confirm`（更新该概念记忆）→ 再提交下一条；也可点「跳过」→ 不调 `confirm`、记忆不变、直接提交下一条（**单条失败即中断**，提示该条重试）。**递归下钻**：用户可在新扩写内容上继续划词 → 立即 `POST /api/optimize/annotation` → **插到待处理队列最前、优先处理**；队列清空才回 `idle`。用户直接输入新问题 → `POST /api/optimize/initial`（**仅优化问题**；`new_topic=true` 时另命名主题）→ 确认（`slug` 只读，且仅新建时展示）→ `POST /api/answer`（**默认并入当前主题追加** / **显式新建时新建文件**）。
- **串行执行模型（T3.6）**：`annotating` 期间以**当前条**（`currentId`）为唯一执行对象——扩写当前条 → 失败则**中断**（不推进队列、仅提示**该条重试**）／成功则**按响应 `anchor_id` 自动滚动到解释处并短暂高亮**（让用户立即看到「解释在哪」；`brief` 落在插入点、`deep` 落在**文末新章节**），再等用户读后点「已理解」或「跳过」→ 移除当前条、推进下一条（无则收尾回 `idle`）。「已理解」与「跳过」在本任务均为**前端本地动作**（`已理解` → `POST /api/memory/confirm` 的接线见 **M4 T4.6**）：不回滚已写扩写、不改记忆。**下钻插队项在「当前条结束后」优先处理**（队首优先）；`currentId` 不随队列插入而漂移（下钻插到队首不影响正在执行的当前条）。每次扩写成功一律以返回的**最新 `note_markdown`** 覆盖前沿状态。
- **候选问题 ↔ 划词原文配对（T3.6）**：`POST /api/optimize/annotation` 的每个候选问题**回带 `snippet_index`**（1-based、对应输入 `snippets` 的编号）；前端**一律按 `snippet_index` 取原文**作为候选项的 **`snippet`**（**前端专有字段、非后端契约**），并在执行时作为 `/api/annotate` 的 **`anchor`**（笔记定位用）——**不依赖输出顺序**（按学习顺序重排也不影响配对）。该字段**不随候选问题文本编辑而变**（编辑只改问题 `text`）。
- **背景展示与刷新**：**页面加载/刷新（=会话开始）时调一次** `POST /api/memory/refresh`；**长会话中途不重算**，设置页提供"刷新背景"按钮可手动再调。设置页显示当前背景，用户可编辑覆盖；一旦手改即置 `background_locked=1`，此后自动推断不再覆盖。
- **跨域**：dev 在 `vite.config.ts` 配 `server.proxy` 将 `/api` 转发到 `http://127.0.0.1:8000`；后端同时挂 `CORSMiddleware` 兜底。

## 9. 测试策略

分层，重点放在**确定性逻辑**，不强行断言 LLM 输出内容：

| 测试文件 | 覆盖 |
|---|---|
| `test_notes.py` | 标注插入定位（**优先词后、兜底行末**）：源码命中 anchor → 插词后；跨行内标记 → 逐行脱标记命中 → 插**该行行末**（多列表项/表格行时紧邻原词所在行，不落到整段末尾）；重复取首次；未命中报错；**名词高亮升级**（Spec FR-15）：行内代码 `` `原词` ``/裸文本/同主题 `[[原词]]` → 就地重建 `==原词==`（已是 `==原词==` 不重复包；落在 `[...](...)`/`**...**`/跨主题 `[[slug#概念]]` 内 → **不重建、降级单向**）；`deep` 把 `####` 新章节**追加到文件末尾**（原地只留括号简释）、末尾带 `[↩]`；**跳转标记**（Spec FR-9c）：`brief` **不做跳转**（只 `==原词==` + 解释侧锚点 + 简释，无链接/来源锚点/「↩」）；`deep` 源码命中 → 原词包 `[==原词==](#anno-{n})` + 来源锚点 `<a id="anno-{n}-src">`、文末章节末尾带 `[↩]`；兜底命中 → **降级单向**（只解释侧锚点、插行末、不重建高亮）；`apply_annotation` 支持传入固定 `anno_id`、`insert_annotation` 返回 `(全文, anchor_id)` |
| `test_memory.py` | 规则：被标注新概念=10（已在库被标注**不改分**）置 pending；「已理解」→ pending=0、`min(100, score+10)`；**重复「已理解」不加分（幂等）**；`score≥80` 即 `mastered` |
| `test_parse.py` | JSON 输出约束与容错：`json_mode` 请求带 `response_format`、容忍 ```` ```json ```` 围栏包裹与多余文字、**取首个 `{` 到配对 `}`（字符串感知，JSON 字符串内嵌的代码围栏/花括号不截断）**、校验不过回喂重试 1 次、字段缺失给默认值 |
| `test_question_optimizer.py` / `test_api.py` | 模式A：**优化版**不含 `{topics}`/不解析 slug/title、**命名版**（`new_topic=true`）喂【现有主题】且 slug/title 为空 → 回喂重试；`/api/optimize/initial` 响应按 `new_topic` 分支（false 只回 `optimized_question`）、命名版 slug **清洗 `[a-z0-9-]` + 去重**（重名加数字后缀） |

**端到端**：demo 阶段以手动验证为主（curl + 浏览器）。
**前端**：demo 阶段暂不引自动化测试，UI 稳定后再补。

## 10. 实现里程碑（对应 Tasks.md，串行执行）

| 里程碑 | 内容 | 验证标准 |
|---|---|---|
| M1 后端核心 | config/llm/notes(段落定位插入)/agents/api 骨架 + `/api/answer` **SSE 流式** + **读接口**（`/api/note/{slug}`、`/api/notes`）+ **模式A 基础版** + JSON 输出约束层 + 单元测试 | curl 能流式看到回答并产出 `data/notes/{slug}.md`，pytest 通过 |
| M2 前端骨架 | NoteView + AskBar/QuestionConfirm + 流式打字机（含 proxy）+ **启动加载**（自动打开最近主题） | 浏览器能提问、确认优化问题与主题、实时看回答流入笔记 |
| M3 标注+问题确认+深度 | 划词标注 + `/api/optimize/annotation` + QuestionConfirm（简单/深入）+ `/api/annotate` | 标注后生成候选问题、确认深度、笔记原地扩写 |
| M4 记忆 | memory 表 + `/memory` + `/memory/confirm`（学懂确认）+ 规则 + 背景设置 | 记忆随标注/确认更新并影响下次回答 |
| M5 主题与内链 | 模式A 命名主题（**用户显式新建时**）、跨主题 `[[slug#概念]]` 正向内链 | 新建主题文件正确创建；springmvc vs flux 并行主题正向引用（反向留 v2） |

## 11. 已确认决策

- **回答即笔记**；标注是对笔记的**原地插入**扩写——**优先词后、兜底行末**（grep 命中源码 → 插到划词之后；跨行内标记 → 逐行脱标记定位 → 插**该行行末**）。
- **笔记结构**：`# {title}` + 若干 **Q/A 区块**（`## {问题}` + 回答正文）；新提问追加 Q/A 区块，标注扩写在该区块**内部**原地插入。
- **自由提问**统一先过模式A（**仅优化问题**）：**默认并入当前打开的主题**（追加 Q/A 区块）；需要新主题时经**主题列表的「新建笔记」入口**显式新建（模式A 命名 slug/title，后端清洗+去重）——**主题归属不由 AI 判定**。
- **主题管理**：左侧**主题列表**（`GET /api/notes`，按 `updated_at` 倒序）可切换已有主题；「新建笔记」进入新主题模式（清空笔记区、AskBar 以 `new_topic=true` 提问）；页面加载仍自动打开最近更新的一篇。
- **大块选区退化追问**：选区（去首尾空白）**> 60 字**自动判为大块 → 退化为**追问**（引用该块走提问区 → 模式A → 确认 → `/api/answer` **末尾追加** Q/A），**不做锚点插入、不写概念记忆**，引用只进 Prompt（【引用片段】）不进 `## 问题` 标题；`≤ 60 字` 走标注；判据**只按字符数、不看跨行**（Spec FR-4a/FR-4b）。
- **保留猜问题环节**（模式B）+ 每个标注单独选**简单/深入**。
- **深入讲解**的新章节**追加到笔记文件最末尾**（原地只留括号简释）；**只有深入了解**才建双向跳转——原词链接跳到底部章节、底部「↩」跳回原词；**简单了解**的简释就在原词旁，**不包链接、不加「↩」**（写了也是空跳）。
- **学习是递归下钻**：问 A 的答案里含 B、C → 标注 B → B 的解释里又含 b1、b2 → …（DFS/后序：先懂叶子，再懂父，最终懂 A）。v1 **由用户交互驱动**该下钻（不自动展开）；**一次一个标注：扩写 → 用户读 → 点「已理解」或「跳过」→ 下一个**，单条失败即中断；用户可在扩写内容上**中途划词插队**（新标注插到队列最前优先处理）。
- **概念记忆**：概念名由**模式B（标注时）**给出，掌握度只由**用户标注**与**「已理解」确认**驱动；**不做 LLM 概念自动提取**，同名/新名由后端精确匹配处理。
- **记忆喂给 AI 分两类**：按 `score` 切分为【已掌握概念】（`≥ 50`）与【尚不熟悉的概念】（`< 50`，多为被标注、尚未学会）；**避免把用户标注过的"不懂的词"当作已掌握**传给回答/优化智能体。
- **掌握度**：分数制 0–100（等级由分数推导）；概念被标注**不改分**（**不在库=10**）并置 pending；点「已理解」（**仅 pending=1 时生效，幂等**）→ pending=0、`min(100, score+10)`；`score≥80` 即 `mastered`；**概念分数只读、不可手改**（`pending` 为内部字段，不返前端）。
- **背景**：**每次会话开始时独立调用一次**——由 `score ≥ 50` 的概念推断"熟悉 X"、由 `score < 50`（多为被标注）推断"正在学 X"；用户设置页可看可改，手改后锁定；初始空串。
- **名词标记**：**首次见的名词=行内代码**（`` `容器` ``）、**本笔记已解释过的名词=`==名词==` 强调高亮**（Typora/Obsidian 通用语法、导出后仍可读）、**跨主题=`[[slug#概念]]`**（v1 只做**正向**且**轻量渲染为标签**、不跳转）；标注扩写完成后**原词就地升级**为 `==名词==`（`deep` 再包 `[==名词==](#anno-{n})` 跳转，Spec FR-15）；反向与跳转/图谱留 v2。
- **标注跳转**：**仅「深入了解」**把**原词 ⇄ 文末深释章节**写成**双向**可点击跳转（原词包 Markdown 链接 `[原词](#anno-{n})` + 来源锚点、文末章节锚点 `<a id="anno-{n}">` + `[↩]` 回跳），**写入笔记源码**、通用 MD 阅读器可点（Spec FR-9c）；**「简单了解」不做任何跳转**（只写 `<a id="anno-{n}"></a>（简释）`）；深入了解时原词无法精确定位则**降级为单向**。前端开 `rehype-raw` 渲染行内 HTML。
- **流式**：真流式（SSE）+ 纯 Markdown，**无分隔符协议**。
- **前端**：无独立「AI 回答区」；回答流式直接进**笔记区**，另有**提问/问题确认区**（模式A 问题确认 + 模式B 候选问题复用）与设置页；`slug` 只读展示。
- **笔记导出**：**纯前端**（用内存中的 `noteMarkdown` 造 Blob 下载，**不新增接口**）；文件名取主题标题（清洗 `\` `/` `:` `*` `?` `"` `<` `>` `|`，为空回退 `{slug}.md`）；流式期间禁用（Spec FR-13b）。
- **LLM**：OpenAI SDK，`base_url=https://api.deepseek.com`；**统一使用 `MODEL=deepseek-flash`**。
- **开发顺序**：严格串行 M1→M2。
- **依赖**：后端追加 `openai`、`fastapi`、`uvicorn`、`python-dotenv`、`pytest`。

## 12. 已知问题与 v1 处理边界

> 本节登记 review 中识别出的问题，标注「当前实现」或「v1 不处理（留 v2）」，避免遗忘；v1 不为此过度设计。

| # | 问题 | 处理 |
|---|---|---|
| 1 | **slug 撞名 / 非法**：模式A 命名时可能产出重复或含非法字符的 slug | `slug` **用户不可改**；后端把 `slug` **清洗为 `[a-z0-9-]`**（仅小写字母/数字/连字符，非法字符 → `-`、折叠并去首尾）并对已有主题**去重**（重复则加 `-2`/`-3` 数字后缀）。模式A **不再产出"并入"回退**（新建主题由用户显式触发） |
| 2 | **`anchor` 多次出现**：同一词在笔记中出现多次，用户标的未必是首次 | 后端**取首次出现的段落**；v1 接受，前端可提示用户选更独特的片段 |
| 3 | **划词↔Markdown 源码映射**：脱标记后模糊匹配非精确 | v1 用"**源码 grep 优先 + 逐段脱标记兜底**"的段落级定位；表格/嵌套列表/代码块内等极端排版可能定位不准，v1 接受 |
| 4 | **反向引用 / 点击跳转 / 知识图谱** | v2（v1 只做正向内链的轻量高亮渲染） |
| 5 | **概念同义/层级合并**（"低耦合" vs "耦合"） | v1 只做**精确名匹配**，合并留后续批量清理 |
| 6 | **掌握度时间衰减**（遗忘降级） | v2 |
| 7 | **`MODEL=deepseek-flash` 的 ID 正确性** | 以官网为准；若实际 ID 不同，只改 `.env`，不影响代码 |
| 8 | **大块阈值（>60 字）是启发式**：整段引用后追问、若用户留空问题，`## 问题` 标题即默认文案「请详细讲解这段内容」，多次此类追问会出现同名标题 | v1 接受（用户可自行输入问题以区分）；阈值**只按字符数、不看跨行**（Spec FR-4a） |
| 9 | **笔记双写无事务**（MD 文件 + `notes` 表） | demo 阶段不做回滚，失败时**以文件为准** |
| 10 | **`pending` 可能长期不消解**：概念被标注后若用户不点「已理解」，会一直是 `pending` | v1 不处理超时/降级，留待后续 |
| 11 | **多用户** | v1 单用户；**v2 扩展方式**：各表加 `user_id` 列 + 读写带 user 条件（v1 不预置该列） |
| 12 | **`questions` / `annotations` 记录表** | v1 对行为历史"只写不读"：`questions` 在 `/api/answer`、`/api/annotate` 写入；`annotations` 在 `/api/annotate` 写入（note_id/snippet/concept/depth）。均无消费接口，**保留表结构**留作历史，查询/展示留 v2 |
| 13 | **`notes.file_path` 冗余列** | 路径恒为 `data/notes/{slug}.md`、可由 slug 推导。**保留该列**（容忍冗余）：为未来换存储位置/嵌套目录留出免改代码的空间 |
| 14 | **记忆只覆盖"用户标注过"的概念**：未标注的概念不进 `concepts` | 设计如此——不做 LLM 概念自动提取、不臆断"未标注即已懂"；概念随标注自然积累 |
| 15 | **首次会话背景恒为空**：背景仅在启动时推断，而概念靠标注累积 → 第一次启动 `concepts` 必空 → `background` 为空串 | v1 接受：第二次启动起背景有效；长会话中途可用设置页"刷新背景"手动再调 |
| 16 | **撤销 / 删除标注** | v1 **不做**（失败可重试该条；「跳过」只跳过确认与队列推进、**不回滚**已写入的扩写内容），留 v2 |
| 17 | **清洗可能误伤代码围栏内的 `#`** | v1 **不特殊处理**（不跳过 ``` 围栏），接受极端排版可能出错 |
| 18 | **标注双向跳转 / 高亮升级的边界**：同一原词被多次标注（链接嵌套）、原词本身已在行内标记内 | v1：源码精确命中且原词**可安全重建**（行内代码/裸文本/同主题 `[[原词]]`）→ **就地升级为 `==原词==`**（Spec FR-15）；落在 `[...](...)`/`**...**`/跨主题 `[[slug#概念]]` 内 → **降级为单向**（只解释侧锚点、不重建高亮）；同词多次标注可能产生嵌套或指向首个链接，接受，v2 再优化 |
