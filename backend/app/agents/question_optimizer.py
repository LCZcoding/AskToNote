"""问题优化智能体 — 模式A（Plan §6.3）/ 模式B（Plan §6.4）。

- 模式A：组装【当前主题/现有主题/背景/已掌握/尚不熟悉/用户问题】→ `chat_json`（JSON 输出约束见 §7），
  返回校验后的 `OptimizeInitialResult{optimized_question, slug, title}`。
- 模式B：组装【笔记全文/标注片段/背景/已有概念】→ `chat_json`，
  返回校验后的 `OptimizeAnnotationResult{questions:[{snippet_index, text, concept, kind, reason}]}`（一标注一问题、不合并）。
"""
from collections.abc import Callable, Iterable, Mapping

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


def _validate_snippet_indices(
    snippet_count: int,
) -> Callable[[OptimizeAnnotationResult], None]:
    """构造「`snippet_index` 须恰好覆盖 1..N 各一次」的业务校验（Plan §6.4）。

    配对一律以 `snippet_index` 为准（顺序可因学习顺序重排），故编号必须一一对应全部片段；
    不通过时抛 `ValueError` —— 由 `chat_json` 带上错误信息**回喂重试**。
    """
    expected = list(range(1, snippet_count + 1))

    def _check(result: OptimizeAnnotationResult) -> None:
        indices = sorted(q.snippet_index for q in result.questions)
        if indices != expected:
            raise ValueError(
                f"questions 的 snippet_index 必须恰好覆盖 1..{snippet_count} 各一次，实际为 {indices}"
            )

    return _check


def optimize_annotation(
    llm: LLMClient,
    *,
    snippets: Iterable[str],
    note_markdown: str,
    background: str,
    concepts_existing: Iterable[str],
) -> OptimizeAnnotationResult:
    """模式B：为每个标注片段猜测候选问题（一标注一问题、不合并，Plan §6.4）。

    `concepts_existing` 为**仅名称清单**，使 LLM 优先复用已有概念名；每个候选问题**回带
    `snippet_index`**（1-based），并校验其**恰好覆盖 1..N**（不通过则回喂重试）；
    解析/校验失败由 `chat_json` 触发回喂重试，两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。
    """
    snippet_list = list(snippets)
    user = render_optimize_annotation_prompt(
        note_markdown=note_markdown,
        snippets=snippet_list,
        background=background,
        concepts_existing=concepts_existing,
    )
    return chat_json(
        llm,
        OPTIMIZE_ANNOTATION_SYSTEM_PROMPT,
        user,
        OptimizeAnnotationResult,
        validate=_validate_snippet_indices(len(snippet_list)),
    )
