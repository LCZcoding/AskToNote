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
) -> str:
    """渲染模式①作答的 user prompt（§6.1）。`slug` 为空时原样保留（空 = 新会话）。"""
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
        },
    )
