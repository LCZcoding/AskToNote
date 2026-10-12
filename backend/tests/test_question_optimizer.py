"""T1.10 模式A 单测（Plan §6.3 / §7）：Prompt 渲染 + 智能体（JSON 调用）。

用 FakeLLM 记录 prompt、返回预设 JSON，不发起真实调用。
"""
import json

import pytest

from app.agents.question_optimizer import (
    OPTIMIZE_ANNOTATION_SYSTEM_PROMPT,
    OPTIMIZE_INITIAL_NEW_TOPIC_SYSTEM_PROMPT,
    OPTIMIZE_INITIAL_SYSTEM_PROMPT,
    optimize_annotation,
    optimize_initial,
)
from app.parse import JsonParseError, OptimizeAnnotationResult, OptimizeInitialResult
from app.prompts import (
    render_optimize_annotation_prompt,
    render_optimize_initial_new_topic_prompt,
    render_optimize_initial_prompt,
)


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
# Prompt 渲染（§6.3 + §6 渲染约定）：模式A 两种模板
# ---------------------------------------------------------------------------


def test_render_optimize_only_question_includes_question_context_and_no_topics() -> None:
    """「优化版」问题/引用片段进入 Prompt；**不含** `{topics}` 占位符（不喂主题、不论归属）。"""
    prompt = render_optimize_initial_prompt(
        question="什么是容器",
        background="熟悉 Java 后端",
        concepts_mastered=[{"name": "容器", "level": "familiar"}],
        concepts_learning=[{"name": "镜像", "level": "unfamiliar"}],
        context="引用块",
    )
    assert "{question}" not in prompt and "{context}" not in prompt
    assert "{topics}" not in prompt and "{slug}" not in prompt
    assert "【用户问题】什么是容器" in prompt
    assert "【用户背景】熟悉 Java 后端" in prompt
    assert "容器(familiar)" in prompt and "镜像(unfamiliar)" in prompt
    assert "【引用片段】引用块" in prompt
    assert "【现有主题】" not in prompt


def test_render_new_topic_includes_topics() -> None:
    """「命名版」含【现有主题】（仅用于 slug 去重），并保留问题与引用片段。"""
    prompt = render_optimize_initial_new_topic_prompt(
        question="什么是容器",
        topics=[{"slug": "docker", "title": "Docker 入门"}],
        background="熟悉 Java 后端",
        concepts_mastered=[],
        concepts_learning=[],
        context="引用块",
    )
    assert "{topics}" not in prompt and "{question}" not in prompt
    assert "【现有主题】docker: Docker 入门" in prompt
    assert "【用户问题】什么是容器" in prompt
    assert "【引用片段】引用块" in prompt


def test_render_optimize_empty_optional_values_use_placeholder() -> None:
    """优化版：背景/两类概念/引用片段为空 → 统一渲染「（暂无）」（共 4 处）。"""
    prompt = render_optimize_initial_prompt(
        question="Q",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
    )
    assert prompt.count("（暂无）") == 4


def test_render_new_topic_empty_optional_values_use_placeholder() -> None:
    """命名版：主题/背景/两类概念/引用片段为空 → 统一渲染「（暂无）」（共 5 处）。"""
    prompt = render_optimize_initial_new_topic_prompt(
        question="Q",
        topics=[],
        background="",
        concepts_mastered=[],
        concepts_learning=[],
    )
    assert prompt.count("（暂无）") == 5


# ---------------------------------------------------------------------------
# 智能体：chat_json 调用与透传
# ---------------------------------------------------------------------------


def test_agent_optimize_only_returns_question_without_naming() -> None:
    """走优化版：`chat(json_mode=True)`、system = 优化版提示词、**不产出** slug/title。"""
    fake = FakeLLM([json.dumps({"optimized_question": "优化后的问题"})])
    out = optimize_initial(
        fake,
        question="什么是容器",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        context="引用块",
    )
    assert isinstance(out, OptimizeInitialResult)
    assert out.optimized_question == "优化后的问题"
    assert out.slug == "" and out.title == ""
    assert fake.calls[0]["json_mode"] is True
    assert fake.calls[0]["system"] == OPTIMIZE_INITIAL_SYSTEM_PROMPT
    assert "【用户问题】什么是容器" in fake.calls[0]["user"]
    assert "【引用片段】引用块" in fake.calls[0]["user"]
    assert "【现有主题】" not in fake.calls[0]["user"]


def test_agent_new_topic_returns_slug_and_title() -> None:
    """走命名版：system = 命名版提示词、topics 进 Prompt、返回 slug/title。"""
    fake = FakeLLM(
        [json.dumps({"optimized_question": "优化后的问题", "slug": "docker", "title": "Docker 入门"})]
    )
    out = optimize_initial(
        fake,
        question="什么是容器",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        new_topic=True,
        topics=[{"slug": "k8s", "title": "K8s 入门"}],
    )
    assert out.optimized_question == "优化后的问题" and out.slug == "docker"
    assert out.title == "Docker 入门"
    assert fake.calls[0]["system"] == OPTIMIZE_INITIAL_NEW_TOPIC_SYSTEM_PROMPT
    assert "k8s: K8s 入门" in fake.calls[0]["user"]


def test_agent_new_topic_retries_when_slug_empty() -> None:
    """命名版空 slug → 回喂重试 1 次（第二次给全则返回）。"""
    fake = FakeLLM(
        [
            json.dumps({"optimized_question": "Q", "slug": "", "title": "标题"}),
            json.dumps({"optimized_question": "Q", "slug": "docker", "title": "Docker 入门"}),
        ]
    )
    out = optimize_initial(
        fake,
        question="Q",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        new_topic=True,
        topics=[],
    )
    assert out.slug == "docker" and out.title == "Docker 入门"
    assert len(fake.calls) == 2
    assert "slug" in fake.calls[1]["user"]  # 错误信息回喂


def test_agent_raises_json_parse_error_after_two_tries() -> None:
    """两次都非法 → 抛 `JsonParseError`，恰好调用 2 次。"""
    fake = FakeLLM(["坏输出一", "坏输出二"])
    with pytest.raises(JsonParseError):
        optimize_initial(
            fake,
            question="Q",
            background="",
            concepts_mastered=[],
            concepts_learning=[],
        )
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# 模式B：Prompt 渲染 + 智能体（§6.4 + §6 渲染约定）
# ---------------------------------------------------------------------------


def test_render_annotation_numbers_snippets_and_lists_concept_names() -> None:
    """snippets 渲染为编号列表；concepts_existing 渲染为逗号分隔的仅名称清单。"""
    prompt = render_optimize_annotation_prompt(
        note_markdown="# Docker 入门\n\n正文",
        snippets=["容器", "容器是轻量级的"],
        background="熟悉 Java 后端",
        concepts_existing=["容器", "镜像", "低耦合"],
    )
    for token in ("{note_markdown}", "{snippets}", "{background}", "{concepts_existing}"):
        assert token not in prompt
    assert "【笔记全文】# Docker 入门" in prompt
    assert "1. 容器" in prompt and "2. 容器是轻量级的" in prompt
    assert "容器, 镜像, 低耦合" in prompt
    assert "【用户背景】熟悉 Java 后端" in prompt


def test_render_annotation_empty_optional_values_use_placeholder() -> None:
    """笔记/片段/背景/已有概念为空 → 统一渲染「（暂无）」（共 4 处）。"""
    prompt = render_optimize_annotation_prompt(
        note_markdown="", snippets=[], background="", concepts_existing=[]
    )
    assert prompt.count("（暂无）") == 4


def test_annotation_agent_returns_parsed_model_and_uses_json_mode() -> None:
    """调 `chat(json_mode=True)`，返回校验后的 `OptimizeAnnotationResult`；片段编号与概念清单进 Prompt。"""
    fake = FakeLLM(
        [
            json.dumps(
                {
                    "questions": [
                        {"snippet_index": 1, "text": "什么是容器", "concept": "容器", "kind": "concept", "reason": "术语"},
                        {"snippet_index": 2, "text": "为什么说容器是轻量级的", "concept": "容器", "kind": "statement", "reason": "说法"},
                    ]
                }
            )
        ]
    )
    out = optimize_annotation(
        fake,
        snippets=["容器", "容器是轻量级的"],
        note_markdown="# Docker 入门",
        background="熟悉 Java 后端",
        concepts_existing=["容器", "镜像"],
    )
    assert isinstance(out, OptimizeAnnotationResult)
    assert [q.snippet_index for q in out.questions] == [1, 2]
    assert [q.text for q in out.questions] == ["什么是容器", "为什么说容器是轻量级的"]
    assert out.questions[1].kind == "statement"
    call = fake.calls[0]
    assert call["json_mode"] is True
    assert call["system"] == OPTIMIZE_ANNOTATION_SYSTEM_PROMPT
    assert "1. 容器" in call["user"] and "2. 容器是轻量级的" in call["user"]
    assert "容器, 镜像" in call["user"]


def test_annotation_agent_accepts_reordered_indices() -> None:
    """输出顺序可因学习顺序重排；只要 `snippet_index` 覆盖 1..N 即通过（配对以 index 为准）。"""
    fake = FakeLLM(
        [
            json.dumps(
                {
                    "questions": [
                        {"snippet_index": 2, "text": "第二个片段的问题", "concept": "B", "kind": "concept"},
                        {"snippet_index": 1, "text": "第一个片段的问题", "concept": "A", "kind": "concept"},
                    ]
                }
            )
        ]
    )
    out = optimize_annotation(
        fake,
        snippets=["A", "B"],
        note_markdown="",
        background="",
        concepts_existing=[],
    )
    assert [q.snippet_index for q in out.questions] == [2, 1]
    assert len(fake.calls) == 1  # 顺序重排不触发重试


def test_annotation_agent_retries_when_indices_not_covering_all_snippets() -> None:
    """`snippet_index` 未恰好覆盖 1..N（如重复）→ 回喂重试 1 次，第二次正确则返回。"""
    fake = FakeLLM(
        [
            json.dumps(
                {
                    "questions": [
                        {"snippet_index": 1, "text": "重复编号", "concept": "A", "kind": "concept"},
                        {"snippet_index": 1, "text": "重复编号", "concept": "B", "kind": "concept"},
                    ]
                }
            ),
            json.dumps(
                {
                    "questions": [
                        {"snippet_index": 1, "text": "问题一", "concept": "A", "kind": "concept"},
                        {"snippet_index": 2, "text": "问题二", "concept": "B", "kind": "concept"},
                    ]
                }
            ),
        ]
    )
    out = optimize_annotation(
        fake,
        snippets=["A", "B"],
        note_markdown="",
        background="",
        concepts_existing=[],
    )
    assert [q.snippet_index for q in out.questions] == [1, 2]
    assert len(fake.calls) == 2
    assert "snippet_index" in fake.calls[1]["user"]  # 错误信息回喂


def test_annotation_agent_raises_when_indices_still_wrong_after_retry() -> None:
    """两次都没覆盖 1..N → 抛 `JsonParseError`，恰好调用 2 次。"""
    bad = json.dumps(
        {
            "questions": [
                {"snippet_index": 1, "text": "x", "concept": "A", "kind": "concept"}
            ]
        }
    )
    fake = FakeLLM([bad, bad])
    with pytest.raises(JsonParseError):
        optimize_annotation(
            fake,
            snippets=["A", "B"],
            note_markdown="",
            background="",
            concepts_existing=[],
        )
    assert len(fake.calls) == 2


def test_annotation_agent_raises_json_parse_error_after_two_tries() -> None:
    """两次都非法 → 抛 `JsonParseError`，恰好调用 2 次。"""
    fake = FakeLLM(["坏输出一", "坏输出二"])
    with pytest.raises(JsonParseError):
        optimize_annotation(
            fake,
            snippets=["容器"],
            note_markdown="",
            background="",
            concepts_existing=[],
        )
    assert len(fake.calls) == 2
