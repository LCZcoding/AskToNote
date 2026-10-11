"""T1.10 模式A 单测（Plan §6.3 / §7）：Prompt 渲染 + 智能体（JSON 调用）。

用 FakeLLM 记录 prompt、返回预设 JSON，不发起真实调用。
"""
import json

import pytest

from app.agents.question_optimizer import SYSTEM_PROMPT, optimize_initial
from app.parse import JsonParseError, OptimizeInitialResult
from app.prompts import render_optimize_initial_prompt


class FakeLLM:
    """按预设队列返回 JSON 响应、并记录调用参数的假客户端。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if not self.responses:
            raise AssertionError("未预置 JSON 响应")
        return self.responses.pop(0)

    def chat_stream(self, system: str, user: str):
        raise AssertionError("模式A 不应走 chat_stream")


# ---------------------------------------------------------------------------
# Prompt 渲染（§6.3 + §6 渲染约定）
# ---------------------------------------------------------------------------


def test_render_includes_question_and_keeps_empty_slug_raw() -> None:
    """问题进入 Prompt；空 slug 原样保留（不替换为「（暂无）」）；引用片段填入。"""
    prompt = render_optimize_initial_prompt(
        question="什么是容器",
        slug="",  # 空 = 新会话/新主题
        background="熟悉 Java 后端",
        concepts_mastered=[{"name": "容器", "level": "familiar"}],
        concepts_learning=[{"name": "镜像", "level": "unfamiliar"}],
        topics=[{"slug": "docker", "title": "Docker 入门"}],
        context="引用块",
    )
    assert "{question}" not in prompt and "{slug}" not in prompt
    assert "{context}" not in prompt
    assert "【用户问题】什么是容器" in prompt
    assert "【当前主题】（当前主题" in prompt  # 空 slug 原样（后随模板注释）
    assert "【用户背景】熟悉 Java 后端" in prompt
    assert "容器(familiar)" in prompt and "镜像(unfamiliar)" in prompt
    assert "docker: Docker 入门" in prompt
    assert "【引用片段】引用块" in prompt


def test_render_empty_optional_values_use_placeholder() -> None:
    """背景/两类概念/主题/引用片段为空 → 统一渲染「（暂无）」。"""
    prompt = render_optimize_initial_prompt(
        question="Q",
        slug="docker",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        topics=[],
    )
    assert prompt.count("（暂无）") == 5


# ---------------------------------------------------------------------------
# 智能体：chat_json 调用与透传
# ---------------------------------------------------------------------------


def test_agent_returns_parsed_model_and_uses_json_mode() -> None:
    """调 `chat(json_mode=True)`，返回校验后的 `OptimizeInitialResult`。"""
    fake = FakeLLM(
        [json.dumps({"optimized_question": "优化后的问题", "slug": "docker", "title": "Docker 入门"})]
    )
    out = optimize_initial(
        fake,
        question="什么是容器",
        slug="",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        topics=[],
        context="引用块",
    )
    assert isinstance(out, OptimizeInitialResult)
    assert out.optimized_question == "优化后的问题" and out.slug == "docker"
    assert fake.calls[0]["json_mode"] is True
    assert fake.calls[0]["system"] == SYSTEM_PROMPT
    assert "【用户问题】什么是容器" in fake.calls[0]["user"]
    assert "【引用片段】引用块" in fake.calls[0]["user"]


def test_agent_raises_json_parse_error_after_two_tries() -> None:
    """两次都非法 → 抛 `JsonParseError`，恰好调用 2 次。"""
    fake = FakeLLM(["坏输出一", "坏输出二"])
    with pytest.raises(JsonParseError):
        optimize_initial(
            fake,
            question="Q",
            slug="",
            background="",
            concepts_mastered=[],
            concepts_learning=[],
            topics=[],
        )
    assert len(fake.calls) == 2
