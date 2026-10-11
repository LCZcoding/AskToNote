"""解答笔记智能体 — 模式①作答（Plan §6.1）。

组装【场景/背景/已掌握/尚不熟悉/其他主题/当前笔记】→ `chat_stream`，
输出的纯 Markdown 直接作为 SSE `delta` 增量（无结构化包裹，Plan §5）。
"""
from collections.abc import Iterable, Iterator, Mapping

from app.llm import LLMClient
from app.prompts import render_answer_note_prompt

SYSTEM_PROMPT = "你是一个帮助用户学习、并把知识沉淀为结构化 Markdown 笔记的学习助手。"


def answer_note_stream(
    llm: LLMClient,
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
) -> Iterator[str]:
    """模式①作答：返回纯 Markdown 增量迭代器（直接透传 `chat_stream`）。"""
    user = render_answer_note_prompt(
        title=title,
        slug=slug,
        background=background,
        concepts_mastered=concepts_mastered,
        concepts_learning=concepts_learning,
        topics=topics,
        note_markdown=note_markdown,
        question=question,
        context=context,
    )
    return llm.chat_stream(SYSTEM_PROMPT, user)
