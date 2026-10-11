"""T1.2 LLM 抽象层：统一协议 `LLMClient` 与 DeepSeek（OpenAI 兼容）实现。

所有 LLM 调用统一经此模块（Tasks.md §0 / Plan §5）：模型名从配置读取、
不写死在业务代码里。流式仅用于模式①作答（Plan §5「流式说明」）。
"""
from collections.abc import Iterator
from typing import Protocol

from openai import OpenAI


class LLMClient(Protocol):
    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...
    def chat_stream(self, system: str, user: str) -> Iterator[str]: ...


class DeepSeekClient(LLMClient):
    """基于 OpenAI 官方 SDK、`base_url` 指向 DeepSeek 的实现。"""

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        )
        return resp.choices[0].message.content

    def chat_stream(self, system: str, user: str) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self.model,
            stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
