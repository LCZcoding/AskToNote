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
| `slug` / `title` | 主题的英文唯一短名 / 中文显示名（**AI 生成、用户不可改**；后端校验 `slug` 合法+去重） |
| `slug`（请求参数） | 当前主题的 slug（模式A 判断新旧主题；为空表示新会话/新主题） |
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
| `note_markdown` | 笔记全文（Markdown 源码） |
| `background` | 用户背景描述 |
| `delta` / `done` / `error` | SSE 事件类型（`type` 字段）：正文增量 / 流结束 / 出错收尾（`error` 带 `message`） |
| `source` | 问题来源（`initial` / `annotation`） |

### 4.1 接口

| 方法/路径 | 请求 | 响应 |
|---|---|---|
| `POST /api/optimize/initial` | `{ question, slug? }` | `{ optimized_question, slug, title }` |
| `POST /api/optimize/annotation` | `{ snippets[], note_markdown }` | `{ questions:[{text, concept, kind, reason}] }` |
| `POST /api/answer` | `{ question, slug, title }` | **SSE**：`delta`{text} 多次 → `done`{ note_markdown } |
| `POST /api/annotate` | `{ slug, anchor, concept, kind, depth, question? }` | `{ note_markdown }` |
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

**`/api/answer` 说明**：`slug`/`title` 由模式A 给出。`slug` 已存在 → `title` **以 `notes` 表为准（忽略请求值）**，以 **Q/A 区块**（`## {确认后的问题}` + 回答正文）**追加**到该主题笔记末尾；新 `slug` → 新建文件（先写 `# {title}`）并**新建** `notes` 记录。落盘在流结束后进行；两种写入**均刷新 `notes.updated_at`**（新建时同时写 `created_at`）。写入笔记的问题用**用户确认后的优化问题**。

**`/api/annotate` 说明**：定位由后端工具函数完成（**优先词后、兜底段末**；**不做**"全局脱标记→映射回源码坐标"）：
1. 按空行把源码切成**段落**（每段保留源码与其位置）；
2. **grep 优先**：逐段在**源码**里 `find(anchor)`，命中**正文段** → 记录该段内 anchor 的**结束位置**（用于**词后**插入）；
3. **脱标记兜底**：源码搜不到（anchor 跨行内标记，如 `**容器**和**镜像**`）→ 对每段**单独**脱去行内标记（`**`、`_`、`[]()`、行内反引号、行首 `#`）后 `find(anchor)`，命中 → 退化为**段末**插入；
4. 命中仅在**标题行**时改取该标题下**首个正文段**；都找不到 → 报错，前端提示重选；
5. 在插入点插入 `insert_text`；`depth="deep"` 时再把 `new_section` 作为新小标题章节**紧跟该段落之后**插入。

更新后刷新 `notes.updated_at`。**前端一次只提交一个标注**：本条扩写返回、用户读后点「已理解」（`POST /api/memory/confirm`）才提交下一条；用户也可点「跳过」（**前端纯本地动作**：不调 `confirm`、记忆不变、不回滚扩写）直接进入下一条；**单条失败即中断**（后续标注不再处理，前端提示失败的那条重新选择），保证序列一致；每次以后端返回的最新 `note_markdown` 更新前端状态（后端每次重读磁盘最新笔记，避免并发写互相覆盖）。**递归下钻**：用户可在新扩写内容上继续划词，新标注立即 `POST /api/optimize/annotation` 拿候选问题与深度，并**插到待处理队列最前**优先处理（后序：先弄懂叶子再回上一层）。

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

> **占位符渲染约定**：`{topics}` → `"slug: title"`（如 `docker: Docker 入门`）；`{concepts_mastered}` → `score ≥ 50` 的概念，`名称(等级)`（如 `容器(familiar)`）；`{concepts_learning}` → `score < 50` 的概念，`名称(等级)`（如 `镜像(unfamiliar)`）。**概念记忆一律按 `score` 分这两类喂给 AI**——`< 50` 的多为用户标注过、尚未学会的词，**不得当作已掌握**。`{concepts_existing}`（**仅模式B 使用**）→ **全部概念名清单、不含分数/等级**（如 `容器, 镜像, 低耦合`），供模式B **优先复用同名概念**；`{question}`（**仅模式②**）为空时渲染为「（未提供，仅凭片段与上下文）」。**空值约定**：除 `{question}`（见上）与 `{slug}`（空 = 新会话/新主题，见模式A 模板）外，其余占位符为空时**统一渲染为「（暂无）」**——典型：新主题首问的 `{note_markdown}`、首个会话的 `{topics}`/`{background}`/`{concepts_mastered}`/`{concepts_learning}`、模式B 的 `{concepts_existing}`。

### 6.1 解答笔记智能体 — 模式①作答（流式纯 Markdown）

```
你是一个学习助手，帮助用户在学习陌生领域的同时，把知识沉淀成结构化 Markdown 笔记。

【场景】用户正在学习主题「{title}」（slug={slug}）。
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50，多为用户标注过、尚未学会的词，如 "镜像(unfamiliar)"
【其他主题】{topics}   // 形如 "docker: Docker 入门"
【当前笔记】{note_markdown}

【要求】
1. 根据用户背景与掌握度决定讲解深度，必要时用其熟悉领域做类比；对【尚不熟悉的概念】要讲透，**不得当作用户已掌握**。
2. 直接输出 Markdown 格式的回答正文，不要包在代码块里，不要输出 JSON。
3. 首次提及的**本主题新概念**用 [[概念名]] 建立内链。
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
   - depth=deep 时输出 new_section，程序会把它作为新小标题章节（**标题固定 `####`**）、紧跟锚点所在段落之后插入。
   - `insert_text` 的插入位置由后端统一决定（**优先词后、兜底段末**），你无需精确控制。
```

### 6.3 问题优化智能体 — 模式A（初始问题 + 命名主题）

```
你是提问优化与主题命名助手。

【当前主题】{slug}（当前主题 slug；为空表示新会话/新主题）
【现有主题】{topics}   // 形如 "docker: Docker 入门"
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）

【任务】
1. 把用户的问题优化得更清晰、更聚焦。
2. 判断该问题是否属于一个**新主题**（与当前主题核心概念链关联弱，属并行关系）：
   - 是新主题 → 给出英文短名 slug 与中文标题 title，slug **不得与【现有主题】中任何 slug 重复**。
   - 不是 → slug/title 与当前主题一致。

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
4. 每个片段**各给一个问题**（**不合并**相近片段，保证与"每个标注单独选深度"一一对应）；按学习顺序（先基础后深入）排序。

【输出】只输出 JSON：
{"questions": [ {"text": "...", "concept": "...", "kind": "concept", "reason": "..."} ]}
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
POST /api/optimize/initial:（模式A）
  读 slug(当前主题)/主题清单/concepts(按 score 分【已掌握】/【尚不熟悉】)/background → 调优化A → 返回 {optimized_question, slug, title}

POST /api/answer (SSE):
  1. 校验 question/slug/title（slug/title 由模式A 给出）
  2. 读 memory(concepts 按 score 分【已掌握】/【尚不熟悉】+background)、读 notes(当前主题 MD)、列出其他主题(slug/title) 供内链
  3. 调解答笔记智能体 chat_stream() → 逐块作为 delta 事件推送
  4. 流结束：**先清洗正文**——把正文中**行首标题（`#`/`##`/`###`…）一律压为 `###`**（`#`/`##` 由程序掌控，避免与 `## {问题}` 同级混淆；**代码围栏 ``` / ~~~ 内的行除外**，避免把代码里的 `#` 注释误压为标题）；若正文开头是重复的问题标题行则删除；整体被 ``` 代码围栏包裹则去围栏。再以 `## {问题}` + 清洗后正文 组成 Q/A 区块——slug 已存在 → 追加到文件末尾；新 slug → 新建文件（先写 `# {title}`）与 notes 记录；**均刷新 notes.updated_at**（新建时写 created_at）；写 questions(source='initial'，concept 置 NULL)；发 done

POST /api/optimize/annotation:（模式B）
  读 note_markdown + snippets + concepts(**仅名称**) → 调优化B → 返回 questions[]

POST /api/annotate:
  1. 读笔记 → 调解答笔记智能体(模式②) 得 {insert, new_section?}（`question` 可为空——为空时按 §6 渲染约定替换为「（未提供，仅凭片段与上下文）」）
  2. 定位插入点（**优先词后、兜底段末**）→ 插入 insert_text；deep 则紧跟该段落之后插入 new_section 章节（**章节标题固定 `####`**，其 `content` 的行首标题同样**压为 `###`**（**代码围栏内除外**））
  3. 写 memory（**concept 与 kind 均来自请求**，由模式B 给出）+ 写 annotations（note_id/snippet/concept/depth）：概念**分数不变**；**不在库 → score=10**；两种情况**均 pending=1**（`kind=concept` 与 `kind=statement` 同规则）
  4. 返回更新后的 note_markdown；写 questions(source='annotation')

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
3. **兜底层**：解析失败或校验不过 → **把错误信息回喂、重试 1 次**；仍失败则返回错误、前端提示；非关键字段缺失按默认值兜底（见 §9 `test_parse`）。

> 模式①作答是纯 Markdown 流式，**不使用**本约束（否则破坏流式渲染）。

**双写说明**：笔记 MD 文件与 `notes` 表分别写入，demo 阶段不做事务回滚（失败时以文件为准）。

**DB 访问约定（v1）**：由 `memory.py` 统一封装——**每次操作开短连接、用完即关**；所有**写**操作经**进程内全局锁**（`threading.Lock`）**串行化**（保证任何两个写操作不会相撞，不会 `database is locked`）；初始化时设一次 `PRAGMA journal_mode=WAL`。读操作不加锁。理由：单用户下并发极少（用户阅读/标注很慢），此约定是最小代价的保险，避免 vibecoding 到处乱建连接。

**笔记文件写锁**：`notes.py` 内置一把模块级 `threading.Lock`，把"读文件 → 改 → 写文件"整个包成临界区（`/api/answer` 追加、`/api/annotate` 插入都走它）→ 写笔记串行化，避免两请求各读旧内容互相覆盖。锁**必须覆盖读+写两步**，不能只锁写。单用户低频，代价可忽略。

**接口实现约定（并发模型）**：所有后端接口一律用**同步 `def`** 定义（不用 `async def`）——本项目全链路是同步风格（OpenAI 同步 SDK、`sqlite3`、文件读写、`threading.Lock`），同步 `def` 由 FastAPI 自动放入**线程池**执行，等待 LLM / 抢锁只占用工作线程、**不阻塞事件循环**；若写成 `async def`，同步等待会直接卡住事件循环 → **整个服务假死**（单用户下线程池默认约 40 线程，容量充裕）。SSE 流式接口同理：`StreamingResponse` 配**同步生成器**（内部调 `chat_stream()` 并 `yield`），由 Starlette 自动放入线程池逐块取数推送。

**失败处理**：`/api/annotate` 单条失败即中断，**前端提示该条重试**（不推进队列），已成功写入的内容不回滚。

## 8. 前端关键交互

- **启动加载**：页面加载时调 `GET /api/notes`（**按 `updated_at` 倒序**）→ 有主题则**自动打开最近更新的一篇**（"继续上次学习"）；无主题则显示空态、引导提问。
- **页面结构（无独立「AI 回答区」）**：主区为**笔记区**，流式回答**直接追加进笔记**；另有**提问/问题确认区**（同一块区域按状态复用）与**设置页**。
- **流式渲染**：`POST /api/answer` 用 `fetch` + `ReadableStream` 读 SSE（EventSource 不支持 POST）。收 `delta` 直接在**笔记区**累积渲染（当前主题新 Q/A 块实时长出）；收 `done` 用后端返回的 `note_markdown` 覆盖刷新笔记。
- **提问/问题确认区**：
  - 提问态：输入原始问题 → 点「优化」→ `POST /api/optimize/initial` → **就地**展示确认卡（**优化后的问题可编辑**；主题只读显示"并入《title》/新建《title》"，`slug` 不可改）→ 确认 → 发出。
  - 标注态：划词结束后，**整批标注一次性**调模式B、同一区域切换为**候选问题列表** → 逐条确认深度；**下钻的新标注单独再走一遍模式B**（复用同一确认区），并插到队列最前。
- **划词标注**：在**笔记区**、流式结束后且 Markdown 渲染成稳定 DOM 时，用 `window.getSelection()` 捕获选中文本（后端按"**优先词后、兜底段末**"定位，见 §4.1），写入标注列表，符合 Spec FR-4。
- **内链渲染**：`react-markdown` 外挂 `remark-wikilink` 小插件，把 `[[概念名]]` / `[[slug#概念]]` 转成高亮 `<span>`（v1 **不可点击跳转**；跳转/图谱留 v2）。
- **状态（zustand store）**：`annotations[]`、`pendingQuestions[]`、`noteSlug`/`noteMarkdown`、`optimizeCard`、`streaming`。
- **状态流转**：`idle`（可提问/可标注）→ 提交提问 → `optimizing`（模式A 出确认卡）→ 确认 → `streaming`（**禁止标注**，回答流入笔记）→ `done`（可标注）→ 标注收集完 → `optimizing`（模式B 拉候选问题）→ 用户确认 → `annotating`（**一次一个**，用户点「已理解」或「跳过」后进入下一个；单条失败提示该条重试）→ 回到 `idle`。请求进行中禁用对应按钮，避免重复提交与状态错乱。
- **流程驱动**：标注完 → `POST /api/optimize/annotation` → QuestionConfirm（每项含深度选择「简单/深入」）→ 用户确认 → **逐个** `POST /api/annotate`，每条返回后刷新笔记；用户读后点「已理解」→ `POST /api/memory/confirm`（更新该概念记忆）→ 再提交下一条；也可点「跳过」→ 不调 `confirm`、记忆不变、直接提交下一条（**单条失败即中断**，提示该条重试）。**递归下钻**：用户可在新扩写内容上继续划词 → 立即 `POST /api/optimize/annotation` → **插到待处理队列最前、优先处理**；队列清空才回 `idle`。用户直接输入新问题 → `POST /api/optimize/initial` → 确认（`slug` 只读）→ `POST /api/answer`（同主题追加 / 新主题新建）。
- **背景展示与刷新**：**页面加载/刷新（=会话开始）时调一次** `POST /api/memory/refresh`；**长会话中途不重算**，设置页提供"刷新背景"按钮可手动再调。设置页显示当前背景，用户可编辑覆盖；一旦手改即置 `background_locked=1`，此后自动推断不再覆盖。
- **跨域**：dev 在 `vite.config.ts` 配 `server.proxy` 将 `/api` 转发到 `http://127.0.0.1:8000`；后端同时挂 `CORSMiddleware` 兜底。

## 9. 测试策略

分层，重点放在**确定性逻辑**，不强行断言 LLM 输出内容：

| 测试文件 | 覆盖 |
|---|---|
| `test_notes.py` | 标注插入定位（**优先词后、兜底段末**）：源码命中 anchor → 插词后；跨行内标记 → 逐段脱标记命中 → 插段末；重复取首次；未命中报错；deep 在段后插 `####` 新章节 |
| `test_memory.py` | 规则：被标注新概念=10（已在库被标注**不改分**）置 pending；「已理解」→ pending=0、`min(100, score+10)`；**重复「已理解」不加分（幂等）**；`score≥80` 即 `mastered` |
| `test_parse.py` | JSON 输出约束与容错：`json_mode` 请求带 `response_format`、剥离 ```` ```json ```` 包裹、容忍多余文字、校验不过回喂重试 1 次、字段缺失给默认值 |

**端到端**：demo 阶段以手动验证为主（curl + 浏览器）。
**前端**：demo 阶段暂不引自动化测试，UI 稳定后再补。

## 10. 实现里程碑（对应 Tasks.md，串行执行）

| 里程碑 | 内容 | 验证标准 |
|---|---|---|
| M1 后端核心 | config/llm/notes(段落定位插入)/agents/api 骨架 + `/api/answer` **SSE 流式** + **读接口**（`/api/note/{slug}`、`/api/notes`）+ **模式A 基础版** + JSON 输出约束层 + 单元测试 | curl 能流式看到回答并产出 `data/notes/{slug}.md`，pytest 通过 |
| M2 前端骨架 | NoteView + AskBar/QuestionConfirm + 流式打字机（含 proxy）+ **启动加载**（自动打开最近主题） | 浏览器能提问、确认优化问题与主题、实时看回答流入笔记 |
| M3 标注+问题确认+深度 | 划词标注 + `/api/optimize/annotation` + QuestionConfirm（简单/深入）+ `/api/annotate` | 标注后生成候选问题、确认深度、笔记原地扩写 |
| M4 记忆 | memory 表 + `/memory` + `/memory/confirm`（学懂确认）+ 规则 + 背景设置 | 记忆随标注/确认更新并影响下次回答 |
| M5 主题边界 | 模式A 命名主题、开新主题、跨主题 `[[slug#概念]]` 正向内链 | springmvc vs flux 并行主题正向引用（反向留 v2） |

## 11. 已确认决策

- **回答即笔记**；标注是对笔记的**原地插入**扩写——**优先词后、兜底段末**（grep 命中源码 → 插到划词之后；跨行内标记 → 逐段脱标记定位段落 → 插段末）。
- **笔记结构**：`# {title}` + 若干 **Q/A 区块**（`## {问题}` + 回答正文）；新提问追加 Q/A 区块，标注扩写在该区块**内部**原地插入。
- **自由提问**统一先过模式A：同主题 → 追加笔记；关联弱（并行关系）→ 经用户确认后开新主题。
- **保留猜问题环节**（模式B）+ 每个标注单独选**简单/深入**。
- **深入讲解**的新章节**紧跟锚点段落之后**。
- **学习是递归下钻**：问 A 的答案里含 B、C → 标注 B → B 的解释里又含 b1、b2 → …（DFS/后序：先懂叶子，再懂父，最终懂 A）。v1 **由用户交互驱动**该下钻（不自动展开）；**一次一个标注：扩写 → 用户读 → 点「已理解」或「跳过」→ 下一个**，单条失败即中断；用户可在扩写内容上**中途划词插队**（新标注插到队列最前优先处理）。
- **概念记忆**：概念名由**模式B（标注时）**给出，掌握度只由**用户标注**与**「已理解」确认**驱动；**不做 LLM 概念自动提取**，同名/新名由后端精确匹配处理。
- **记忆喂给 AI 分两类**：按 `score` 切分为【已掌握概念】（`≥ 50`）与【尚不熟悉的概念】（`< 50`，多为被标注、尚未学会）；**避免把用户标注过的"不懂的词"当作已掌握**传给回答/优化智能体。
- **掌握度**：分数制 0–100（等级由分数推导）；概念被标注**不改分**（**不在库=10**）并置 pending；点「已理解」（**仅 pending=1 时生效，幂等**）→ pending=0、`min(100, score+10)`；`score≥80` 即 `mastered`；**概念分数只读、不可手改**（`pending` 为内部字段，不返前端）。
- **背景**：**每次会话开始时独立调用一次**——由 `score ≥ 50` 的概念推断"熟悉 X"、由 `score < 50`（多为被标注）推断"正在学 X"；用户设置页可看可改，手改后锁定；初始空串。
- **交叉引用（内链）**：本主题 `[[概念名]]`、跨主题 `[[slug#概念]]`，v1 只做**正向**且**轻量渲染为高亮标签**（不跳转）；反向与跳转/图谱留 v2。
- **流式**：真流式（SSE）+ 纯 Markdown，**无分隔符协议**。
- **前端**：无独立「AI 回答区」；回答流式直接进**笔记区**，另有**提问/问题确认区**（模式A 问题确认 + 模式B 候选问题复用）与设置页；`slug` 只读展示。
- **LLM**：OpenAI SDK，`base_url=https://api.deepseek.com`；**统一使用 `MODEL=deepseek-flash`**。
- **开发顺序**：严格串行 M1→M2。
- **依赖**：后端追加 `openai`、`fastapi`、`uvicorn`、`python-dotenv`、`pytest`。

## 12. 已知问题与 v1 处理边界

> 本节登记 review 中识别出的问题，标注「当前实现」或「v1 不处理（留 v2）」，避免遗忘；v1 不为此过度设计。

| # | 问题 | 处理 |
|---|---|---|
| 1 | **slug 撞名 / 非法**：模式A 可能产出与现有主题重复或含非法字符的 slug | `slug` **用户不可改**（消除手改风险）；后端**校验合法性**（非法字符 → 回退为"并入当前主题"）；与现有主题重复 → 按"已存在→追加"处理（**不会崩**，最坏误并入）。v1 **不**做自动后缀 |
| 2 | **`anchor` 多次出现**：同一词在笔记中出现多次，用户标的未必是首次 | 后端**取首次出现的段落**；v1 接受，前端可提示用户选更独特的片段 |
| 3 | **划词↔Markdown 源码映射**：脱标记后模糊匹配非精确 | v1 用"**源码 grep 优先 + 逐段脱标记兜底**"的段落级定位；表格/嵌套列表/代码块内等极端排版可能定位不准，v1 接受 |
| 4 | **反向引用 / 点击跳转 / 知识图谱** | v2（v1 只做正向内链的轻量高亮渲染） |
| 5 | **概念同义/层级合并**（"低耦合" vs "耦合"） | v1 只做**精确名匹配**，合并留后续批量清理 |
| 6 | **掌握度时间衰减**（遗忘降级） | v2 |
| 7 | **`MODEL=deepseek-flash` 的 ID 正确性** | 以官网为准；若实际 ID 不同，只改 `.env`，不影响代码 |
| 8 | **笔记双写无事务**（MD 文件 + `notes` 表） | demo 阶段不做回滚，失败时**以文件为准** |
| 9 | **`pending` 可能长期不消解**：概念被标注后若用户不点「已理解」，会一直是 `pending` | v1 不处理超时/降级，留待后续 |
| 10 | **多用户** | v1 单用户；**v2 扩展方式**：各表加 `user_id` 列 + 读写带 user 条件（v1 不预置该列） |
| 11 | **`questions` / `annotations` 记录表** | v1 对行为历史"只写不读"：`questions` 在 `/api/answer`、`/api/annotate` 写入；`annotations` 在 `/api/annotate` 写入（note_id/snippet/concept/depth）。均无消费接口，**保留表结构**留作历史，查询/展示留 v2 |
| 12 | **`notes.file_path` 冗余列** | 路径恒为 `data/notes/{slug}.md`、可由 slug 推导。**保留该列**（容忍冗余）：为未来换存储位置/嵌套目录留出免改代码的空间 |
| 13 | **记忆只覆盖"用户标注过"的概念**：未标注的概念不进 `concepts` | 设计如此——不做 LLM 概念自动提取、不臆断"未标注即已懂"；概念随标注自然积累 |
| 14 | **首次会话背景恒为空**：背景仅在启动时推断，而概念靠标注累积 → 第一次启动 `concepts` 必空 → `background` 为空串 | v1 接受：第二次启动起背景有效；长会话中途可用设置页"刷新背景"手动再调 |
| 15 | **撤销 / 删除标注** | v1 **不做**（失败可重试该条；「跳过」只跳过确认与队列推进、**不回滚**已写入的扩写内容），留 v2 |
| 16 | **清洗可能误伤代码围栏内的 `#`** | v1 **不特殊处理**（不跳过 ``` 围栏），接受极端排版可能出错 |
