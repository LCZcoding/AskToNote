"""解答笔记智能体 — 模式①作答（Plan §6.1）/ 模式②标注扩写（Plan §6.2）。

- 模式①作答：组装【场景/背景/已掌握/尚不熟悉/其他主题/当前笔记】→ `chat_stream`，
  输出的纯 Markdown 直接作为 SSE `delta` 增量（无结构化包裹，Plan §5）。
- 模式②标注扩写：组装【笔记全文/标注片段/片段类型/规范概念名/了解深度 + 背景与两类概念】
  → `chat_json`，返回校验后的 `AnnotateResult{insert, new_section?}`（JSON 输出约束见 §7）。
"""
from collections.abc import Iterable, Iterator, Mapping

from app.llm import LLMClient
from app.parse import AnnotateResult, chat_json
from app.prompts import render_annotate_prompt, render_answer_note_prompt

ANSWER_SYSTEM_PROMPT = "你是一个帮助用户学习、并把知识沉淀为结构化 Markdown 笔记的学习助手。"
ANNOTATE_SYSTEM_PROMPT = "你是笔记扩写助手。"


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
    return llm.chat_stream(ANSWER_SYSTEM_PROMPT, user)


def annotate(
    llm: LLMClient,
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
) -> AnnotateResult:
    """模式②标注扩写：返回校验后的 `AnnotateResult`（`brief` 时 `new_section` 为 None）。

    解析/校验失败由 `chat_json` 触发回喂重试，两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。
    """
    user = render_annotate_prompt(
        note_markdown=note_markdown,
        anchor=anchor,
        question=question,
        kind=kind,
        concept=concept,
        depth=depth,
        background=background,
        concepts_mastered=concepts_mastered,
        concepts_learning=concepts_learning,
    )
    return chat_json(llm, ANNOTATE_SYSTEM_PROMPT, user, AnnotateResult)
