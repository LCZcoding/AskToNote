"""Prompt 模板与占位符渲染（Plan §6）。

模板按 Plan §6 逐字落地；渲染遵循 §6「占位符渲染约定」：
- `{topics}` → `"slug: title"`；
- `{concepts_mastered}` / `{concepts_learning}` → `名称(等级)`；
- 空值（`{question}`、`{slug}` 除外）统一渲染为「（暂无）」。
"""
from collections.abc import Iterable, Mapping

NA = "（暂无）"


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


def format_concepts(concepts: Iterable[Mapping[str, str]]) -> str:
    """概念列表 `[{name, level}]` → `名称(等级)`，多概念换行分隔；空 → 「（暂无）」。"""
    items = [f"{c['name']}({c['level']})" for c in concepts]
    return "\n".join(items) if items else NA


def format_topics(topics: Iterable[Mapping[str, str]]) -> str:
    """主题列表 `[{slug, title}]` → `slug: title`，多主题换行分隔；空 → 「（暂无）」。"""
    items = [f"{t['slug']}: {t['title']}" for t in topics]
    return "\n".join(items) if items else NA


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


# --- Plan §6.3 问题优化智能体 — 模式A（初始问题 + 命名主题） --------------------

OPTIMIZE_INITIAL_TEMPLATE = """你是提问优化与主题命名助手。

【当前主题】{slug}（当前主题 slug；为空表示新会话/新主题）
【现有主题】{topics}   // 形如 "docker: Docker 入门"
【用户背景】{background}
【已掌握概念】{concepts_mastered}   // score≥50，如 "容器(familiar)"
【尚不熟悉的概念】{concepts_learning}   // score<50（用户标注过、尚未学会）
【引用片段】{context}   // 用户选中追问的一大块原文，可空
【用户问题】{question}

【任务】
1. 把用户的问题优化得更清晰、更聚焦。
2. 判断该问题是否属于一个**新主题**（与当前主题核心概念链关联弱，属并行关系）：
   - 是新主题 → 给出英文短名 slug 与中文标题 title，slug **不得与【现有主题】中任何 slug 重复**。
   - 不是 → slug/title 与当前主题一致。

【输出】只输出 JSON：{"optimized_question": "...", "slug": "...", "title": "..."}
"""


def render_optimize_initial_prompt(
    *,
    question: str,
    slug: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    topics: Iterable[Mapping[str, str]],
    context: str = "",
) -> str:
    """渲染模式A的 user prompt（§6.3）。

    `slug` 与 `question` **原样保留**（不参与「（暂无）」兜底）：空 slug = 新会话/新主题；
    `question` 为必填入参（见 §6 渲染约定）；`context`（引用片段）为空时渲染为「（暂无）」。
    """
    return _fill(
        OPTIMIZE_INITIAL_TEMPLATE,
        {
            "slug": slug,
            "topics": format_topics(topics),
            "background": _or_na(background),
            "concepts_mastered": format_concepts(concepts_mastered),
            "concepts_learning": format_concepts(concepts_learning),
            "question": question,
            "context": _or_na(context),
        },
    )
