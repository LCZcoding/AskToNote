"""T1.2 LLM 抽象层：统一协议 `LLMClient` 与 DeepSeek（OpenAI 兼容）实现。

所有 LLM 调用统一经此模块（Tasks.md §0 / Plan §5）：模型名从配置读取、
不写死在业务代码里。流式仅用于模式①作答（Plan §5「流式说明」）。

另：每次调用后打印一行 usage（Plan §5「上下文缓存观测」）——DeepSeek 的上下文
缓存是**前缀缓存**，命中率只取决于「最长的稳定前缀」，用于给后续可能的重排建立基线。
"""
from collections.abc import Iterator
from typing import Protocol

from openai import OpenAI

# 模式标签长度：system 开头若干字（各模式角色描述不同，足以区分调用方）。
_TAG_LEN = 16


def _prompt_tag(system: str) -> str:
    """取 system 开头 `_TAG_LEN` 字作模式标签（压缩空白，避免日志换行）。"""
    return " ".join(system.split())[:_TAG_LEN]


def _usage_field(usage: object, name: str) -> int:
    """读 usage 字段；DeepSeek 扩展字段可能落在 `model_extra` 里，缺失记 0。"""
    value = getattr(usage, name, None)
    if value is None:
        value = (getattr(usage, "model_extra", None) or {}).get(name)
    return int(value) if value is not None else 0


def _log_usage(usage: object, system: str) -> None:
    """打印一行上下文缓存命中（Plan §5「上下文缓存观测」）。"""
    hit = _usage_field(usage, "prompt_cache_hit_tokens")
    miss = _usage_field(usage, "prompt_cache_miss_tokens")
    total = hit + miss
    rate = f"{hit / total:.1%}" if total else "n/a"
    prompt = _usage_field(usage, "prompt_tokens")
    print(
        f"[llm-usage] {_prompt_tag(system)} hit={hit} miss={miss} "
        f"rate={rate} prompt={prompt}",
        flush=True,
    )


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
        if resp.usage is not None:
            _log_usage(resp.usage, system)
        return resp.choices[0].message.content

    def chat_stream(self, system: str, user: str) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self.model,
            stream=True,
            # usage 随最后一个 chunk 回传（Plan §5「上下文缓存观测」）。
            stream_options={"include_usage": True},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        for chunk in stream:
            if chunk.usage is not None:
                _log_usage(chunk.usage, system)
            if not chunk.choices:  # 末帧只带 usage、choices 为空
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
