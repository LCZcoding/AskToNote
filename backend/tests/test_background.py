"""T4.3 背景推断智能体单测：占位符渲染 + JSON 产出解析（mock LLM，不真实计费）。

覆盖：§6.5 模板三类占位符填充（概念两档 `名称(等级)`、主题 `slug: title`）、
空值统一渲染为「（暂无）」、`background` 解析（含空串）、`chat_json` 走 `json_mode`。
"""
import app.prompts as prompts
from app.agents import background
from app.parse import BackgroundResult


class FakeJsonLLM:
    """按预设字符串返回 `chat` 结果、记录调用参数的假客户端。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls: list[dict] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        return self.raw

    def chat_stream(self, system: str, user: str):  # pragma: no cover - 背景推断不走流式
        raise AssertionError("背景推断不应走流式 chat_stream")


def _render(**overrides) -> str:
    values = {
        "concepts_mastered": [{"name": "JVM", "level": "familiar"}],
        "concepts_learning": [{"name": "镜像", "level": "unfamiliar"}],
        "topics": [{"slug": "docker", "title": "Docker 入门"}],
    }
    values.update(overrides)
    return prompts.render_background_prompt(**values)


def _infer(fake: FakeJsonLLM, **overrides) -> BackgroundResult:
    values = {
        "concepts_mastered": [],
        "concepts_learning": [],
        "topics": [],
    }
    values.update(overrides)
    return background.infer_background(fake, **values)


def test_render_background_fills_all_placeholders() -> None:
    """§6.5 各占位符按渲染约定填入，模板无残留占位符（`{background}` 是 JSON 示例、非占位符）。"""
    user = _render()
    assert "【已掌握概念】JVM(familiar)" in user
    assert "【尚不熟悉的概念】镜像(unfamiliar)" in user
    assert "【已有主题】docker: Docker 入门" in user
    for key in ("concepts_mastered", "concepts_learning", "topics"):
        assert "{" + key + "}" not in user


def test_render_background_empty_values_use_na() -> None:
    """三类占位符空值统一渲染为「（暂无）」。"""
    user = _render(concepts_mastered=[], concepts_learning=[], topics=[])
    assert user.count(prompts.NA) == 3


def test_infer_background_parses_result() -> None:
    """解析出 `background`；调用带 `json_mode` 与系统提示词，三类输入进入 prompt。"""
    fake = FakeJsonLLM('{"background": "熟悉 Java 后端"}')
    result = _infer(
        fake,
        concepts_mastered=[{"name": "JVM", "level": "familiar"}],
        concepts_learning=[{"name": "镜像", "level": "unfamiliar"}],
        topics=[{"slug": "docker", "title": "Docker 入门"}],
    )
    assert result.background == "熟悉 Java 后端"
    call = fake.calls[0]
    assert call["json_mode"] is True
    assert call["system"] == background.BACKGROUND_SYSTEM_PROMPT
    assert "【已掌握概念】JVM(familiar)" in call["user"]
    assert "【已有主题】docker: Docker 入门" in call["user"]


def test_infer_background_allows_empty_background() -> None:
    """无法推断时返回空串（Plan §6.5 要求 2）。"""
    fake = FakeJsonLLM('{"background": ""}')
    assert _infer(fake).background == ""