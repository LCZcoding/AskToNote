"""背景推断智能体 — 会话级一次（Plan §6.5）。

组装【已掌握概念】/【尚不熟悉的概念】/【已有主题（仅标题）】→ `chat_json`，
返回 `BackgroundResult{background}`（无法明显推断时为空字符串）。
由 `POST /api/memory/refresh` 调用；是否写回由路由层按 `background_locked` 决定。
"""
from collections.abc import Iterable, Mapping

from app.llm import LLMClient
from app.parse import BackgroundResult, chat_json
from app.prompts import render_background_prompt

BACKGROUND_SYSTEM_PROMPT = "你是用户背景推断助手。"


def infer_background(
    llm: LLMClient,
    *,
    concepts_mastered: Iterable[Mapping[str, str]],
    concepts_learning: Iterable[Mapping[str, str]],
    topics: Iterable[Mapping[str, str]],
) -> BackgroundResult:
    """背景推断（Plan §6.5）：由概念两档与主题清单推断用户背景。

    解析/校验失败由 `chat_json` 回喂重试，两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。
    """
    user = render_background_prompt(
        concepts_mastered=concepts_mastered,
        concepts_learning=concepts_learning,
        topics=topics,
    )
    return chat_json(llm, BACKGROUND_SYSTEM_PROMPT, user, BackgroundResult)