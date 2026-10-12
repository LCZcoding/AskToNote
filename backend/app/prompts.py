"""Prompt 模板与占位符渲染（Plan §6）。

模板按 Plan §6 逐字落地；渲染遵循 §6「占位符渲染约定」：
- `{topics}` → `"slug: title"`；
- `{concepts_mastered}` / `{concepts_learning}` → `名称(等级)`；
- 空值（`{slug}`、`{question}` 除外）统一渲染为「（暂无）」；`{question}` 为空时渲染为
  QUESTION_NA（「（未提供，仅凭片段与上下文）」）。
"""
from collections.abc import Iterable, Mapping

NA = "（暂无）"
# `{question}` 的专用空值默认（Plan §6 渲染约定；模式② 的 question 可选）。
QUESTION_NA = "（未提供，仅凭片段与上下文）"


def _fill(template: str, values: Mapping[str, str]) -> str:
    """按 `{key}` 逐一替换（用 replace 而非 str.format，避免模板里的 JSON 花括号冲突）。"""
    text = template
    for key, val in values.items():
        text = text.replace("{" + key + "}", val)
    return text


def _or_na(value: str | None) -> str:
    """空字符串/空白 → 「（暂无）」。"""
    text = (value or "").strip()
    return text if text else NA


def _or_question_na(value: str | None) -> str:
    """`{question}` 空值 → QUESTION_NA（Plan §6 渲染约定）。"""
    text = (value or "").strip()
    return text if text else QUESTION_NA


def format_concepts(concepts: Iterable[Mapping[str, str]]) -> str:
    """概念列表 `[{name, level}]` → `名称(等级)`，多概念换行分隔；空 → 「（暂无）」。"""
    items = [f"{c['name']}({c['level']})" for c in concepts]
    return "\n".join(items) if items else NA


def format_topics(topics: Iterable[Mapping[str, str]]) -> str:
    """主题列表 `[{slug, title}]` → `slug: title`，多主题换行分隔；空 → 「（暂无）」。"""
    items = [f"{t['slug']}: {t['title']}" for t in topics]
    return "\n".join(items) if items else NA


def format_snippets(snippets: Iterable[str]) -> str:
    """标注片段列表 → 编号列表 `1. 片段`（每片段一行）；空 → 「（暂无）」（Plan §6.4）。"""
    items = [f"{i}. {s}" for i, s in enumerate(snippets, start=1)]
    return "\n".join(items) if items else NA


def format_concept_names(names: Iterable[str]) -> str:
    """概念名清单 → 逗号分隔（`容器, 镜像`，**仅名称**不含分数/等级）；空 → 「（暂无）」（Plan §6.4）。"""
    items = [str(n) for n in names]
    return ", ".join(items) if items else NA


# --- Plan §6.1 解答笔记智能体 — 模式①作答（流式纯 Markdown） -------------------

ANSWER_NOTE_TEMPLATE = """你是一个学习助手，帮助用户在学习陌生领域的同时，把知识沉淀成结构化 Markdown 笔记。

【场景】用户正在学习主题「{title}」（slug={slug}）。
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50，多为用户标注过、尚未学会的词，如 "镜像(unfamiliar)"
【其他主题】{topics}   // 形如 "docker: Docker 入门"
【当前笔记】{note_markdown}
【引用片段】{context}   // 用户选中追问的一大块原文，可空
【用户问题】{question}

【要求】
1. 根据用户背景与掌握度决定讲解深度，必要时用其熟悉领域做类比；对【尚不熟悉的概念】要讲透，**不得当作用户已掌握**。
2. 直接输出 Markdown 格式的回答正文，不要包在代码块里，不要输出 JSON。
3. 首次提及的**本主题新概念**用 [[概念名]] 建立内链。
4. 提及**属于其他主题**的概念时用 [[该主题slug#概念名]]，slug 只能取自【其他主题】清单。
5. 只输出**本次回答的正文**；问题标题由程序统一添加，你**不要**自己写 `#`/`##` 问题标题。
"""


def render_answer_note_prompt(
    *,
    title: str,
    slug: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    topics: Iterable[Mapping[str, str]],
    note_markdown: str,
    question: str,
    context: str = "",
) -> str:
    """渲染模式①作答的 user prompt（§6.1）。

    `slug` 与 `question` **原样保留**（空 slug = 新会话；`question` 为必填入参）；
    `context`（引用片段）为空时渲染为「（暂无）」。
    """
    return _fill(
        ANSWER_NOTE_TEMPLATE,
        {
            "title": _or_na(title),
            "slug": slug,
            "background": _or_na(background),
            "concepts_mastered": format_concepts(concepts_mastered),
            "concepts_learning": format_concepts(concepts_learning),
            "topics": format_topics(topics),
            "note_markdown": _or_na(note_markdown),
            "question": question,
            "context": _or_na(context),
        },
    )


# --- Plan §6.2 解答笔记智能体 — 模式②标注扩写（JSON） -------------------------

ANNOTATE_TEMPLATE = """你是笔记扩写助手。用户在笔记中标注了陌生片段，请生成局部的解释内容。

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
   - `insert_text` **必须整体用全角括号（）包裹**、写成一句旁注（如 `（Docker 是一个容器化工具）`），**不得**写成独立句子或分点列表——它是插进正文的简释、要让读者一眼看出「这是标注解释」。
   - `insert_text` 的插入位置由后端统一决定（**优先词后、兜底段末**），你无需精确控制。
"""


def render_annotate_prompt(
    *,
    note_markdown: str,
    anchor: str,
    kind: str,
    concept: str,
    depth: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    question: str = "",
) -> str:
    """渲染模式②标注扩写的 user prompt（§6.2）。

    `question`（可选）为空时渲染为 QUESTION_NA（Plan §6 渲染约定）；
    其余占位符空值统一渲染为「（暂无）」。
    """
    return _fill(
        ANNOTATE_TEMPLATE,
        {
            "note_markdown": _or_na(note_markdown),
            "anchor": _or_na(anchor),
            "question": _or_question_na(question),
            "kind": _or_na(kind),
            "concept": _or_na(concept),
            "background": _or_na(background),
            "concepts_mastered": format_concepts(concepts_mastered),
            "concepts_learning": format_concepts(concepts_learning),
            "depth": _or_na(depth),
        },
    )


# --- Plan §6.3 问题优化智能体 — 模式A（优化问题；显式新建时命名主题） -----------
# 模式A 有**两种** prompt，**永不判断"新建 vs 并入"**（主题归属由用户决定）。

# 优化版（默认，`new_topic=False`）：只优化问题，不喂 topics、不产出 slug/title。
OPTIMIZE_INITIAL_TEMPLATE = """你是提问优化助手。

【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）
【用户问题】{question}
【引用片段】{context}   // 用户选中追问的一大块原文，可空

【任务】把用户的问题优化得更清晰、更聚焦。
【输出】只输出 JSON：{"optimized_question": "..."}
"""

# 命名版（`new_topic=True`）：优化问题 + 命名新主题（喂【现有主题】仅供 slug 避免重名）。
OPTIMIZE_INITIAL_NEW_TOPIC_TEMPLATE = """你是提问优化与主题命名助手。

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
"""


def render_optimize_initial_prompt(
    *,
    question: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    context: str = "",
) -> str:
    """渲染模式A「优化版」的 user prompt（§6.3）：只优化问题、**不喂 topics**。

    `question` 为必填入参、原样保留；`context`（引用片段）为空时渲染为「（暂无）」。
    """
    return _fill(
        OPTIMIZE_INITIAL_TEMPLATE,
        {
            "background": _or_na(background),
            "concepts_mastered": format_concepts(concepts_mastered),
            "concepts_learning": format_concepts(concepts_learning),
            "question": question,
            "context": _or_na(context),
        },
    )


def render_optimize_initial_new_topic_prompt(
    *,
    question: str,
    topics: Iterable[Mapping[str, str]],
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    context: str = "",
) -> str:
    """渲染模式A「命名版」的 user prompt（§6.3）：优化问题 + 命名新主题（喂【现有主题】供去重）。"""
    return _fill(
        OPTIMIZE_INITIAL_NEW_TOPIC_TEMPLATE,
        {
            "topics": format_topics(topics),
            "background": _or_na(background),
            "concepts_mastered": format_concepts(concepts_mastered),
            "concepts_learning": format_concepts(concepts_learning),
            "question": question,
            "context": _or_na(context),
        },
    )


# --- Plan §6.4 问题优化智能体 — 模式B（标注 → 候选问题） ------------------------

OPTIMIZE_ANNOTATION_TEMPLATE = """你是提问助手。用户在笔记中标注了一些陌生片段，请猜测针对每个片段用户真正想问什么。

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
"""


def render_optimize_annotation_prompt(
    *,
    note_markdown: str,
    snippets: Iterable[str],
    background: str,
    concepts_existing: Iterable[str],
) -> str:
    """渲染模式B的 user prompt（§6.4）。

    `{snippets}` → **编号列表**（一标注一问题、按序对应）；`{concepts_existing}` →
    **逗号分隔的仅名称清单**（供 LLM 优先复用同名概念）；空值统一渲染为「（暂无）」。
    """
    return _fill(
        OPTIMIZE_ANNOTATION_TEMPLATE,
        {
            "note_markdown": _or_na(note_markdown),
            "snippets": format_snippets(snippets),
            "background": _or_na(background),
            "concepts_existing": format_concept_names(concepts_existing),
        },
    )
