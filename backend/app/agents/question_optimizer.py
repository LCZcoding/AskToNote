"""问题优化智能体 — 模式A（Plan §6.3）。

组装【当前主题/现有主题/背景/已掌握/尚不熟悉/用户问题】→ `chat_json`（JSON 输出约束见 §7），
返回校验后的 `OptimizeInitialResult{optimized_question, slug, title}`。
"""
from collections.abc import Iterable, Mapping

from app.llm import LLMClient
from app.parse import OptimizeInitialResult, chat_json
from app.prompts import render_optimize_initial_prompt

SYSTEM_PROMPT = "你是一个提问优化与主题命名助手。"


def optimize_initial(
    llm: LLMClient,
    *,
    question: str,
    slug: str,
    background: str,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    topics: Iterable[Mapping[str, str]],
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
    )
    return chat_json(llm, SYSTEM_PROMPT, user, OptimizeInitialResult)
