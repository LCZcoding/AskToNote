"""问题优化智能体 — 模式A（Plan §6.3）/ 模式B（Plan §6.4）。

- 模式A：组装【当前主题/现有主题/背景/已掌握/尚不熟悉/用户问题】→ `chat_json`（JSON 输出约束见 §7），
  返回校验后的 `OptimizeInitialResult{optimized_question, slug, title}`。
- 模式B：组装【笔记全文/标注片段/背景/已有概念】→ `chat_json`，
  返回校验后的 `OptimizeAnnotationResult{questions:[{text, concept, kind, reason}]}`（一标注一问题、不合并）。
"""
from collections.abc import Iterable, Mapping

from app.llm import LLMClient
from app.parse import OptimizeAnnotationResult, OptimizeInitialResult, chat_json
from app.prompts import (
    render_optimize_annotation_prompt,
    render_optimize_initial_prompt,
)

OPTIMIZE_INITIAL_SYSTEM_PROMPT = "你是一个提问优化与主题命名助手。"
OPTIMIZE_ANNOTATION_SYSTEM_PROMPT = "你是提问助手。"


def optimize_initial(
    llm: LLMClient,
    *,
    question: str,
    slug: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    topics: Iterable[Mapping[str, str]],
    context: str = "",
) -> OptimizeInitialResult:
    """模式A：优化初始问题并命名主题（新主题给新 slug，否则沿用当前）。

    解析/校验失败由 `chat_json` 触发回喂重试，两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。
    """
    user = render_optimize_initial_prompt(
        question=question,
        slug=slug,
        background=background,
        concepts_mastered=concepts_mastered,
        concepts_learning=concepts_learning,
        topics=topics,
        context=context,
    )
    return chat_json(llm, OPTIMIZE_INITIAL_SYSTEM_PROMPT, user, OptimizeInitialResult)


def optimize_annotation(
    llm: LLMClient,
    *,
    snippets: Iterable[str],
    note_markdown: str,
    background: str,
    concepts_existing: Iterable[str],
) -> OptimizeAnnotationResult:
    """模式B：为每个标注片段猜测候选问题（一标注一问题、不合并）。

    `concepts_existing` 为**仅名称清单**，使 LLM 优先复用已有概念名；
    解析/校验失败由 `chat_json` 触发回喂重试，两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。
    """
    user = render_optimize_annotation_prompt(
        note_markdown=note_markdown,
        snippets=snippets,
        background=background,
        concepts_existing=concepts_existing,
    )
    return chat_json(llm, OPTIMIZE_ANNOTATION_SYSTEM_PROMPT, user, OptimizeAnnotationResult)
