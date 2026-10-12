"""T1.8 JSON 输出约束与解析容错单测（Plan §7「JSON 输出约束」/ §9 `test_parse`）。

覆盖：`json_mode` 调用、容忍 ```` ```json ```` 围栏与多余文字、**字符串感知提取**（JSON 字符串
内嵌的代码围栏/花括号不截断）、Pydantic 校验、非关键字段缺失默认值兜底、校验不过**回喂错误
重试 1 次**、两次仍失败抛 `JsonParseError`。全部用 FakeLLM，不发起真实调用。
"""
import pytest
from pydantic import ValidationError

import app.parse as parse
from app.parse import (
    AnnotateResult,
    BackgroundResult,
    CandidateQuestion,
    OptimizeAnnotationResult,
    OptimizeInitialResult,
)


class FakeLLM:
    """按预设队列返回响应、并记录调用参数的假客户端。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if not self.responses:
            raise AssertionError("响应队列已空，说明调用次数超出预期")
        return self.responses.pop(0)

    def chat_stream(self, system: str, user: str):
        raise AssertionError("JSON 调用不应走 chat_stream")


# ---------------------------------------------------------------------------
# 解析容错：围栏 / 多余文字
# ---------------------------------------------------------------------------


def test_extract_json_plain() -> None:
    """纯 JSON 原样提取。"""
    assert parse.extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_tolerates_json_fence() -> None:
    """容忍整体 ```` ```json ```` 围栏包裹。"""
    assert parse.extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_tolerates_bare_fence() -> None:
    """容忍整体无语言标注的 ```` ``` ```` 围栏包裹。"""
    assert parse.extract_json("```\n{\"a\": 1}\n```") == '{"a": 1}'


def test_extract_json_tolerates_surrounding_text() -> None:
    """容忍 JSON 前后的多余文字。"""
    assert parse.extract_json('好的，结果如下：\n{"a": 1}\n以上。') == '{"a": 1}'


def test_extract_json_keeps_fences_inside_string_values() -> None:
    """JSON 字符串值内嵌 Markdown 代码围栏 → **不得截断**（T3.13 根因回归）。

    模式② 的 `new_section.content` 是 Markdown、常含 ```` ```python ```` 代码围栏；
    且围栏里的代码还含 `{`/`}`——字符串感知扫描必须把整段忽略掉。
    """
    text = (
        '{"insert": {"insert_text": "（x）"}, "new_section": {"title": "深入", '
        '"content": "### 它是什么\\n\\n```python\\nchain = prompt | llm | parser\\n'
        'prompt.invoke({\\"q\\": 1})\\n```\\n\\n结尾。"}}'
    )
    extracted = parse.extract_json(text)
    assert extracted == text  # 完整对象，未被首个 ``` 截断
    out = parse.parse_model(text, AnnotateResult)
    assert out.new_section is not None
    assert "```python" in out.new_section.content
    assert out.new_section.content.endswith("结尾。")


def test_extract_json_handles_braces_and_escaped_quotes_in_strings() -> None:
    """字符串内的花括号与 `\\"` 转义不干扰配对（不会提前把对象判为结束）。"""
    text = '{"background": "值里有 } 和 { 还有 \\" 引号", "b": 2}'
    assert parse.extract_json(text) == text
    assert (
        parse.parse_model(text, BackgroundResult).background
        == '值里有 } 和 { 还有 " 引号'
    )


def test_extract_json_raises_when_no_object() -> None:
    """找不到 JSON 对象时抛 ValueError。"""
    with pytest.raises(ValueError):
        parse.extract_json("没有任何大括号")


def test_extract_json_raises_when_unbalanced() -> None:
    """括号不配对（输出被截断）时抛 ValueError。"""
    with pytest.raises(ValueError):
        parse.extract_json('{"a": 1')


# ---------------------------------------------------------------------------
# Pydantic 校验：四个调用的模型
# ---------------------------------------------------------------------------


def test_parse_optimize_initial() -> None:
    """模式A：三字段解析成功。"""
    out = parse.parse_model(
        '{"optimized_question": "什么是容器", "slug": "docker", "title": "Docker 入门"}',
        OptimizeInitialResult,
    )
    assert out.optimized_question == "什么是容器"
    assert out.slug == "docker"
    assert out.title == "Docker 入门"


def test_parse_optimize_annotation() -> None:
    """模式B：候选问题列表（含 snippet_index/text/concept/kind/reason）。"""
    out = parse.parse_model(
        '{"questions": [{"snippet_index": 1, "text": "什么是镜像", "concept": "镜像", "kind": "concept", "reason": "用户标注了镜像"}]}',
        OptimizeAnnotationResult,
    )
    assert out.questions[0].snippet_index == 1
    assert out.questions[0].text == "什么是镜像"
    assert out.questions[0].kind == "concept"


def test_parse_annotate_brief_has_no_new_section() -> None:
    """模式② brief：无 new_section → 默认 None。"""
    out = parse.parse_model(
        '{"insert": {"insert_text": "（轻量级虚拟化）"}}', AnnotateResult
    )
    assert out.insert.insert_text == "（轻量级虚拟化）"
    assert out.new_section is None


def test_parse_annotate_deep_has_new_section() -> None:
    """模式② deep：new_section 解析成功。"""
    out = parse.parse_model(
        '{"insert": {"insert_text": "（x）"}, "new_section": {"title": "镜像", "content": "只读模板"}}',
        AnnotateResult,
    )
    assert out.new_section is not None
    assert out.new_section.title == "镜像"
    assert out.new_section.content == "只读模板"


def test_chat_json_parses_annotate_with_code_fence_in_content() -> None:
    """回归（T3.13）：模式② 的 `new_section.content` 含代码围栏时，`chat_json` 仍解析成功、不触发重试。"""
    raw = (
        '{"insert": {"insert_text": "（Output Parser 是转换器）"}, "new_section": '
        '{"title": "深入理解 Output Parser", "content": "### 它是什么\\n\\n```python\\n'
        'chain = prompt | llm | parser\\n```\\n\\n结尾。"}}'
    )
    fake = FakeLLM([raw])
    out = parse.chat_json(fake, "SYS", "USER", AnnotateResult)
    assert out.new_section is not None
    assert "```python" in out.new_section.content
    assert len(fake.calls) == 1  # 一次成功，没有走回喂重试


def test_optional_fields_fall_back_to_defaults() -> None:
    """非关键字段缺失按默认值兜底（候选问题 reason/kind、背景）。"""
    q = CandidateQuestion.model_validate({"snippet_index": 1, "text": "什么是容器"})
    assert q.concept == ""
    assert q.kind == "concept"
    assert q.reason == ""

    assert BackgroundResult.model_validate({}).background == ""


def test_invalid_kind_raises_validation_error() -> None:
    """kind 不在枚举内 → 校验失败。"""
    with pytest.raises(ValidationError):
        CandidateQuestion.model_validate({"snippet_index": 1, "text": "x", "kind": "术语"})


def test_missing_required_field_raises_validation_error() -> None:
    """缺少关键字段（如 optimized_question）→ 校验失败。"""
    with pytest.raises(ValidationError):
        OptimizeInitialResult.model_validate({"slug": "docker", "title": "Docker"})


def test_candidate_question_missing_snippet_index_raises() -> None:
    """候选问题缺 `snippet_index`（关键字段）→ 校验失败，触发回喂重试（Plan §6.4）。"""
    with pytest.raises(ValidationError):
        CandidateQuestion.model_validate({"text": "什么是容器"})


# ---------------------------------------------------------------------------
# chat_json：json_mode + 回喂重试
# ---------------------------------------------------------------------------


def test_chat_json_calls_with_json_mode() -> None:
    """`chat_json` 必须以 `json_mode=True` 调用（Plan §7 第 1 层）。"""
    fake = FakeLLM(['{"background": "熟悉 Java"}'])
    out = parse.chat_json(fake, "SYS", "USER", BackgroundResult)
    assert out.background == "熟悉 Java"
    assert fake.calls[0]["json_mode"] is True
    assert fake.calls[0]["system"] == "SYS"
    assert fake.calls[0]["user"] == "USER"


def test_chat_json_retries_once_with_error_feedback_then_succeeds() -> None:
    """首次非法 → 回喂错误重试 1 次；第二次成功返回。"""
    fake = FakeLLM(["这不是 JSON", '{"background": "正在学 Docker"}'])
    out = parse.chat_json(fake, "SYS", "原始问题", BackgroundResult)
    assert out.background == "正在学 Docker"
    assert len(fake.calls) == 2
    # 第二次的 user 里带上了原始问题与错误反馈
    assert "原始问题" in fake.calls[1]["user"]
    assert "错误信息" in fake.calls[1]["user"]


def test_chat_json_raises_after_two_failures() -> None:
    """两次都失败 → 抛 JsonParseError，且恰好调用 2 次。"""
    fake = FakeLLM(["坏输出一", "坏输出二"])
    with pytest.raises(parse.JsonParseError):
        parse.chat_json(fake, "SYS", "USER", BackgroundResult)
    assert len(fake.calls) == 2


def test_chat_json_retries_on_validation_error() -> None:
    """校验不过（而非 JSON 语法错误）也会触发回喂重试。"""
    fake = FakeLLM(
        ['{"questions": [{"snippet_index": 1, "text": "x", "kind": "不是枚举"}]}',
         '{"questions": [{"snippet_index": 1, "text": "x", "kind": "statement"}]}']
    )
    out = parse.chat_json(fake, "SYS", "USER", OptimizeAnnotationResult)
    assert out.questions[0].kind == "statement"
    assert len(fake.calls) == 2


def test_chat_json_runs_extra_validate_and_retries_on_failure() -> None:
    """`validate` 业务校验不通过 → 同样回喂重试；第二次通过则返回（Plan §6.4 覆盖 1..N）。"""
    fake = FakeLLM(
        [
            '{"questions": [{"snippet_index": 1, "text": "x"}]}',
            '{"questions": [{"snippet_index": 1, "text": "x"}, {"snippet_index": 2, "text": "y"}]}',
        ]
    )

    def _need_two(result: OptimizeAnnotationResult) -> None:
        if len(result.questions) != 2:
            raise ValueError("必须恰好 2 条")

    out = parse.chat_json(
        fake, "SYS", "USER", OptimizeAnnotationResult, validate=_need_two
    )
    assert len(out.questions) == 2
    assert len(fake.calls) == 2
    assert "必须恰好 2 条" in fake.calls[1]["user"]
