"""T1.6 模式①作答智能体单测：占位符渲染 + 流式产出（mock LLM，不真实计费）。

覆盖：占位符填充、空值渲染为「（暂无）」、概念/主题格式化、`chat_stream` 透传
（输出即原始增量，无 JSON/代码块包裹）。
"""
import app.prompts as prompts
from app.agents import answer_note


class FakeLLM:
    """记录调用参数、按预设分块流式返回的假客户端。"""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = list(chunks)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        raise AssertionError("模式①作答不应走非流式 chat")

    def chat_stream(self, system: str, user: str):
        self.calls.append((system, user))
        return iter(self.chunks)


def _render(**overrides) -> str:
    values = {
        "title": "Docker 入门",
        "slug": "docker",
        "background": "熟悉 Java 后端",
        "concepts_mastered": [{"name": "容器", "level": "familiar"}],
        "concepts_learning": [{"name": "镜像", "level": "unfamiliar"}],
        "topics": [{"slug": "docker", "title": "Docker 入门"}],
        "note_markdown": "# Docker 入门\n",
        "question": "什么是容器",
        "context": "",
    }
    values.update(overrides)
    return prompts.render_answer_note_prompt(**values)


def test_render_fills_all_placeholders() -> None:
    """各占位符按 §6 渲染约定填入。"""
    user = _render(context="大块引用原文")
    assert "「Docker 入门」" in user
    assert "（slug=docker）" in user
    assert "熟悉 Java 后端" in user
    assert "容器(familiar)" in user
    assert "镜像(unfamiliar)" in user
    assert "docker: Docker 入门" in user
    assert "# Docker 入门" in user
    assert "【用户问题】什么是容器" in user
    assert "【引用片段】大块引用原文" in user
    # 模板本身不再残留未替换的占位符
    assert "{title}" not in user and "{note_markdown}" not in user and "{question}" not in user
    assert "{context}" not in user


def test_render_empty_values_use_na_placeholder() -> None:
    """空值统一渲染为「（暂无）」；`slug` 例外（空 = 新会话）。"""
    user = _render(
        slug="",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
        topics=[],
        note_markdown="",
    )
    assert user.count(prompts.NA) == 6  # background/mastered/learning/topics/note/context
    assert "（slug=）" in user  # slug 为空时不替换为占位


def test_render_includes_cross_topic_wikilink_rule_and_other_topics() -> None:
    """T5.2 跨主题正向内链：模式① Prompt 含 `[[该主题slug#概念名]]` 规则，
    且【其他主题】清单把每个可选 slug 都渲染出来（供模型填写内链 slug）。"""
    user = _render(
        topics=[
            {"slug": "flux", "title": "Flux 响应式"},
            {"slug": "java", "title": "Java 基础"},
        ]
    )
    # 规则 4：跨主题用 [[slug#概念名]]（本主题用 [[概念名]]），slug 只能取自清单
    assert "[[该主题slug#概念名]]" in user
    # 【其他主题】清单把全部可选 slug 都渲染出来（多主题按行拼接），模型据此产出 [[flux#概念]] 形式内链
    topics_line = next(l for l in user.splitlines() if l.startswith("【其他主题】"))
    assert topics_line.split("//")[0].strip() == "【其他主题】flux: Flux 响应式"
    assert "java: Java 基础" in user


def test_format_concepts_and_topics_join_multiple() -> None:
    """多概念/多主题按行拼接；空列表 → 「（暂无）」。"""
    assert (
        prompts.format_concepts(
            [{"name": "容器", "level": "familiar"}, {"name": "镜像", "level": "unfamiliar"}]
        )
        == "容器(familiar)\n镜像(unfamiliar)"
    )
    assert prompts.format_concepts([]) == prompts.NA
    assert (
        prompts.format_topics(
            [{"slug": "docker", "title": "Docker 入门"}, {"slug": "java", "title": "Java 基础"}]
        )
        == "docker: Docker 入门\njava: Java 基础"
    )
    assert prompts.format_topics([]) == prompts.NA


def test_answer_note_stream_passes_prompt_and_yields_raw_chunks() -> None:
    """流式函数把渲染后的 prompt 交给 `chat_stream`，并原样透传增量（无包裹）。"""
    fake = FakeLLM(["# 标题", "\n\n容器", "是轻量级虚拟化。"])
    out = list(
        answer_note.answer_note_stream(
            fake,
            title="Docker 入门",
            slug="docker",
            background="",
            concepts_mastered=[],
            concepts_learning=[],
            topics=[],
            note_markdown="",
            question="什么是容器",
            context="引用块",
        )
    )
    assert out == ["# 标题", "\n\n容器", "是轻量级虚拟化。"]
    system, user = fake.calls[0]
    assert system  # system 非空
    assert "「Docker 入门」" in user
    assert "【用户问题】什么是容器" in user
    assert "【引用片段】引用块" in user
    assert prompts.NA in user  # 空背景等已渲染
