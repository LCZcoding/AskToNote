"""T1.8 JSON 输出约束与解析容错（Plan §7「JSON 输出约束」）。

三层约束（适用于模式A / 模式B / 模式② / 背景推断）：
1. API 层——调用 `llm.chat(json_mode=True)`，带 `response_format={"type":"json_object"}`
   （实现见 `app/llm.py`）；
2. 校验层——按 Pydantic 模型校验字段与类型（模型 schema 取自 Plan §6）；
3. 兜底层——解析容错（剥离 ```json 围栏、容忍首尾多余文字）+ 校验不过**回喂错误重试 1 次**；
   非关键字段缺失按默认值兜底；两次仍失败抛 `JsonParseError`（由调用方转成前端提示）。

模式①作答是纯 Markdown 流式，**不使用**本模块。
"""
import json
import re
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import LLMClient

# ```json ... ``` 或 ``` ... ``` 围栏（可跨行）。
_FENCED_RE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)\n?```", re.S)


class JsonParseError(Exception):
    """两次尝试仍无法得到合法且校验通过的 JSON。"""


# ---------------------------------------------------------------------------
# Plan §6 四个 JSON 调用的 Pydantic 模型
# ---------------------------------------------------------------------------


class OptimizeInitialResult(BaseModel):
    """模式A（§6.3）：优化后问题（`slug`/`title` 仅**命名版** `new_topic=true` 时给出）。"""

    optimized_question: str
    slug: str = ""
    title: str = ""


class CandidateQuestion(BaseModel):
    """模式B（§6.4）：单个候选问题；`concept`/`reason`/`kind` 为非关键字段、有默认值。

    `snippet_index`（1-based）为**关键字段**——指明该候选问题所答的片段在输入 `snippets`
    中的编号；缺失即视为契约不符、触发回喂重试（配对一律以它为准，Plan §6.4/§8）。
    """

    snippet_index: int
    text: str
    concept: str = ""
    kind: Literal["concept", "statement"] = "concept"
    reason: str = ""


class OptimizeAnnotationResult(BaseModel):
    """模式B（§6.4）整体输出。"""

    questions: list[CandidateQuestion]


class NewSection(BaseModel):
    """模式②（§6.2）：深入讲解的新章节。"""

    title: str
    content: str


class Insert(BaseModel):
    """模式②（§6.2）：括号简释正文。"""

    insert_text: str


class AnnotateResult(BaseModel):
    """模式②（§6.2）整体输出；`brief` 时无 `new_section`（默认 None）。"""

    insert: Insert
    new_section: NewSection | None = None


class BackgroundResult(BaseModel):
    """背景推断（§6.5）；无法推断时为空字符串。"""

    background: str = ""


# ---------------------------------------------------------------------------
# 解析容错
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """若含 ``` 围栏则取第一段围栏内内容，否则原样去空白。"""
    m = _FENCED_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def extract_json(text: str) -> str:
    """从模型输出中提取 JSON 文本：剥离 ```json 围栏 + 容忍首尾多余文字。

    取「首个 `{` 到最后一个 `}`」的片段；找不到对象时抛 `ValueError`。
    """
    body = _strip_code_fence(text)
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"响应中未找到 JSON 对象：{text!r}")
    return body[start : end + 1]


TModel = TypeVar("TModel", bound=BaseModel)


def parse_model(text: str, model: type[TModel]) -> TModel:
    """单次解析 + 校验：容错提取 JSON → `json.loads` → Pydantic 校验。"""
    data: Any = json.loads(extract_json(text))
    return model.model_validate(data)


_RETRY_SUFFIX = "\n\n【上一次输出无法解析，错误信息】{err}\n请严格按照要求，仅输出合法 JSON。"


def chat_json(
    llm: LLMClient,
    system: str,
    user: str,
    model: type[TModel],
    validate: Callable[[TModel], None] | None = None,
) -> TModel:
    """调 `chat(json_mode=True)` → 解析校验 →（可选）业务校验；不过则回喂错误重试 1 次，仍失败抛 `JsonParseError`。

    `validate` 用于**无法在模型字段里表达的业务校验**（如模式B 的 `snippet_index` 须恰好覆盖 1..N）：
    它抛出的异常与 Pydantic 校验失败同等对待，带上错误信息回喂重试。
    """
    last_error: Exception | None = None
    current_user = user
    for _ in range(2):
        raw = llm.chat(system, current_user, json_mode=True)
        try:
            result = parse_model(raw, model)
            if validate is not None:
                validate(result)
            return result
        except (ValueError, ValidationError) as exc:  # 语法/提取/字段校验/业务校验任一不过
            last_error = exc
            current_user = user + _RETRY_SUFFIX.format(err=exc)
    raise JsonParseError(str(last_error)) from last_error
