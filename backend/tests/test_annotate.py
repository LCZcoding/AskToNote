"""T3.1 模式②标注扩写智能体单测：占位符渲染 + JSON 产出解析（mock LLM，不真实计费）。

覆盖：§6.2 模板占位符填充、空值渲染（`question` 走专用默认、其余「（暂无）」）、
`brief` 无 `new_section`、`deep` 含 `new_section`、`chat_json` 走 `json_mode`。
"""
import app.prompts as prompts
from app.agents import answer_note
from app.parse import AnnotateResult


class FakeJsonLLM:
    """按预设字符串返回 `chat` 结果、记录调用参数的假客户端。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls: list[dict] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        return self.raw

    def chat_stream(self, system: str, user: str):  # pragma: no cover - 模式②不走流式
        raise AssertionError("模式②标注扩写不应走流式 chat_stream")


def _render(**overrides) -> str:
    values = {
        "note_markdown": "# Docker 入门\n\n容器是轻量级的虚拟化单元。\n",
        "anchor": "容器",
        "question": "容器到底是什么",
        "kind": "concept",
        "concept": "容器",
        "depth": "brief",
        "background": "熟悉 Java 后端",
        "concepts_mastered": [{"name": "JVM", "level": "familiar"}],
        "concepts_learning": [{"name": "镜像", "level": "unfamiliar"}],
    }
    values.update(overrides)
    return prompts.render_annotate_prompt(**values)


def _annotate(fake: FakeJsonLLM, **overrides) -> AnnotateResult:
    values = {
        "note_markdown": "# Docker 入门\n",
        "anchor": "容器",
        "question": "容器到底是什么",
        "kind": "concept",
        "concept": "容器",
        "depth": "brief",
        "background": "",
        "concepts_mastered": [],
        "concepts_learning": [],
    }
    values.update(overrides)
    return answer_note.annotate(fake, **values)


def test_render_annotate_fills_all_placeholders() -> None:
    """§6.2 各占位符按渲染约定填入，模板无残留占位符。"""
    user = _render()
    assert "【笔记全文】# Docker 入门" in user
    assert "【用户标注的片段】容器" in user
    assert "【针对该片段的问题】容器到底是什么" in user
    assert "【片段类型】concept" in user
    assert "【规范概念名】容器" in user
    assert "熟悉 Java 后端" in user
    assert "JVM(familiar)" in user
    assert "镜像(unfamiliar)" in user
    assert "【了解深度】brief" in user
    for key in (
        "note_markdown",
        "anchor",
        "question",
        "kind",
        "concept",
        "background",
        "concepts_mastered",
        "concepts_learning",
        "depth",
    ):
        assert "{" + key + "}" not in user


def test_render_annotate_empty_question_uses_special_default() -> None:
    """模式② `question` 可选：为空时渲染为专用默认（Plan §6 渲染约定）。"""
    user = _render(question="")
    assert f"【针对该片段的问题】{prompts.QUESTION_NA}" in user


def test_render_annotate_empty_values_use_na() -> None:
    """`question` 之外的占位符空值统一渲染为「（暂无）」。"""
    user = _render(
        note_markdown="",
        anchor="",
        concept="",
        background="",
        concepts_mastered=[],
        concepts_learning=[],
    )
    # note_markdown/anchor/background/mastered/learning 各 1 处，concept 出现 2 处
    # （【规范概念名】与【要求】第 1 条），合计 7
    assert user.count(prompts.NA) == 7
    assert prompts.QUESTION_NA not in user


def test_annotate_brief_parses_result_without_new_section() -> None:
    """`brief`：解析出 `insert.insert_text`，且 `new_section` 为 None。"""
    fake = FakeJsonLLM('{"insert": {"insert_text": "（容器是轻量级的虚拟化单元）"}}')
    result = _annotate(fake, depth="brief")
    assert isinstance(result, AnnotateResult)
    assert result.insert.insert_text == "（容器是轻量级的虚拟化单元）"
    assert result.new_section is None
    call = fake.calls[0]
    assert call["json_mode"] is True
    assert call["system"] == answer_note.ANNOTATE_SYSTEM_PROMPT
    assert "【用户标注的片段】容器" in call["user"]
    assert "【了解深度】brief" in call["user"]


def test_annotate_deep_parses_new_section() -> None:
    """`deep`：解析出 `new_section{title, content}`。"""
    raw = (
        '{"insert": {"insert_text": "（简述）"}, '
        '"new_section": {"title": "深入理解", "content": "### 可写层"}}'
    )
    fake = FakeJsonLLM(raw)
    result = _annotate(fake, depth="deep", question="")
    assert result.insert.insert_text == "（简述）"
    assert result.new_section is not None
    assert result.new_section.title == "深入理解"
    assert result.new_section.content == "### 可写层"
    # 空 question 也进入 prompt（走专用默认）
    assert f"【针对该片段的问题】{prompts.QUESTION_NA}" in fake.calls[0]["user"]


def test_annotate_renders_kind_statement_and_concept() -> None:
    """`kind=statement` 与规范概念名进入 prompt。"""
    fake = FakeJsonLLM('{"insert": {"insert_text": "（解释该说法）"}}')
    _annotate(fake, kind="statement", concept="低耦合")
    user = fake.calls[0]["user"]
    assert "【片段类型】statement" in user
    assert "【规范概念名】低耦合" in user
