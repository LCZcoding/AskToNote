"""T1.2 LLM 抽象层单测：验证 chat / chat_stream 行为与传给 OpenAI SDK 的参数。

全部用例通过 monkeypatch 把 `app.llm.OpenAI` 换成假客户端，**不发起真实网络请求**
（不产生计费）。真实调用的验证见 Tasks.md T1.2 的文档验证步骤（Plan §5）。

T3.15 补充：usage / 上下文缓存命中埋点（Plan §5「上下文缓存观测」）——假 usage 断言
打印内容与 `stream_options`，仍不发真实请求。
"""
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

import app.llm as llm


def _chunk(text, usage=None):
    """构造一个流式 chunk：choices[0].delta.content = text。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=usage
    )


def _usage_chunk(usage):
    """构造只带 usage 的末帧：choices 为空（Plan §5「上下文缓存观测」）。"""
    return SimpleNamespace(choices=[], usage=usage)


def _completion(content, usage=None):
    """构造一个非流式响应：choices[0].message.content = content。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage
    )


def _usage(hit, miss, prompt):
    """构造 DeepSeek 风格的 usage（含缓存扩展字段）。"""
    return SimpleNamespace(
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
        prompt_tokens=prompt,
    )


@pytest.fixture
def fake_openai(monkeypatch):
    """安装假 OpenAI 客户端；返回可读写的 state（init/calls/content/chunks）。"""
    state: dict = {"init": None, "calls": [], "content": "pong", "chunks": []}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            state["init"] = kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        def _create(self, **kwargs):
            state["calls"].append(kwargs)
            if kwargs.get("stream"):
                return iter(state["chunks"])
            return _completion(state["content"], state.get("usage"))

    monkeypatch.setattr(llm, "OpenAI", _FakeOpenAI)
    return state


def test_init_passes_key_and_base_url(fake_openai) -> None:
    """构造时应把 api_key / base_url 透传给 OpenAI 客户端。"""
    llm.DeepSeekClient(api_key="sk-test", model="deepseek-flash", base_url="https://api.deepseek.com")
    assert fake_openai["init"] == {
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com",
    }


def test_stores_model(fake_openai) -> None:
    """模型名应保存为实例属性，供各方法复用。"""
    client = llm.DeepSeekClient("k", "deepseek-flash", "u")
    assert client.model == "deepseek-flash"


def test_protocol_methods_present() -> None:
    """DeepSeekClient 应实现协议要求的两个方法。"""
    assert hasattr(llm.DeepSeekClient, "chat")
    assert hasattr(llm.DeepSeekClient, "chat_stream")


def test_chat_returns_content_string(fake_openai) -> None:
    """chat 应返回模型输出的字符串正文。"""
    fake_openai["content"] = "pong"
    client = llm.DeepSeekClient("k", "m", "u")

    out = client.chat("sys", "usr")

    assert isinstance(out, str)
    assert out == "pong"


def test_chat_sends_model_and_messages(fake_openai) -> None:
    """chat 应把 model 与 system/user 消息按契约传出。"""
    client = llm.DeepSeekClient("k", "deepseek-flash", "u")

    client.chat("SYS", "USR")

    call = fake_openai["calls"][0]
    assert call["model"] == "deepseek-flash"
    assert call["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_chat_json_mode_sets_response_format(fake_openai) -> None:
    """json_mode=True 应带 response_format=json_object（Plan §5）。"""
    client = llm.DeepSeekClient("k", "m", "u")

    client.chat("s", "u", json_mode=True)

    assert fake_openai["calls"][0]["response_format"] == {"type": "json_object"}


def test_chat_non_json_mode_omits_response_format(fake_openai) -> None:
    """json_mode=False 不应传 response_format。"""
    client = llm.DeepSeekClient("k", "m", "u")

    client.chat("s", "u")

    assert "response_format" not in fake_openai["calls"][0]


def test_chat_stream_returns_iterator(fake_openai) -> None:
    """chat_stream 应返回迭代器（惰性产出）。"""
    fake_openai["chunks"] = [_chunk("a")]
    client = llm.DeepSeekClient("k", "m", "u")

    it = client.chat_stream("s", "u")

    assert isinstance(it, Iterator)


def test_chat_stream_yields_nonempty_deltas(fake_openai) -> None:
    """chat_stream 应逐块产出非空增量，跳过空/None 增量。"""
    fake_openai["chunks"] = [_chunk("Hel"), _chunk(""), _chunk(None), _chunk("lo")]
    client = llm.DeepSeekClient("k", "m", "u")

    out = list(client.chat_stream("s", "u"))

    assert out == ["Hel", "lo"]


def test_chat_stream_sends_stream_true(fake_openai) -> None:
    """chat_stream 应以 stream=True 调用，且不传 response_format。"""
    fake_openai["chunks"] = [_chunk("a")]
    client = llm.DeepSeekClient("k", "m", "u")

    list(client.chat_stream("s", "u"))

    call = fake_openai["calls"][0]
    assert call["stream"] is True
    assert "response_format" not in call
    assert call["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


# --- T3.15 埋点：usage / 上下文缓存命中（Plan §5「上下文缓存观测」） --------------


def test_prompt_tag_is_single_line_and_bounded() -> None:
    """模式标签取 system 前 16 字、压缩空白（多行 system 不能让日志断行）。"""
    tag = llm._prompt_tag("你是笔记扩写助手。\n\n用户在笔记中标注了陌生片段")
    assert "\n" not in tag
    assert len(tag) <= llm._TAG_LEN
    assert tag.startswith("你是笔记扩写助手。")


def test_usage_field_reads_attribute() -> None:
    """命中字段在属性上时应直接读出。"""
    usage = SimpleNamespace(prompt_cache_hit_tokens=100)
    assert llm._usage_field(usage, "prompt_cache_hit_tokens") == 100


def test_usage_field_falls_back_to_model_extra() -> None:
    """DeepSeek 扩展字段可能不落在属性上，而在 pydantic 的 `model_extra` 里。"""
    usage = SimpleNamespace(model_extra={"prompt_cache_hit_tokens": 7})
    assert llm._usage_field(usage, "prompt_cache_hit_tokens") == 7


def test_usage_field_missing_is_zero() -> None:
    """字段不存在时记 0，不得抛异常。"""
    assert llm._usage_field(SimpleNamespace(), "prompt_cache_miss_tokens") == 0


def test_chat_logs_cache_hit_rate(fake_openai, capsys) -> None:
    """chat 调用后应打印一行命中率。"""
    fake_openai["usage"] = _usage(hit=1024, miss=3072, prompt=4096)
    client = llm.DeepSeekClient("k", "m", "u")

    client.chat("你是笔记扩写助手。", "u")

    out = capsys.readouterr().out
    assert "你是笔记扩写助手。" in out
    assert "hit=1024" in out
    assert "miss=3072" in out
    assert "rate=25.0%" in out
    assert "prompt=4096" in out


def test_chat_logs_na_rate_without_cache_fields(fake_openai, capsys) -> None:
    """响应不带缓存字段时命中率记 `n/a`，不得除零崩掉。"""
    fake_openai["usage"] = SimpleNamespace(prompt_tokens=10)
    client = llm.DeepSeekClient("k", "m", "u")

    client.chat("你是提问助手。", "u")

    out = capsys.readouterr().out
    assert "hit=0 miss=0" in out
    assert "rate=n/a" in out


def test_chat_stream_requests_usage_and_logs_final_chunk(fake_openai, capsys) -> None:
    """流式应带 `stream_options={"include_usage": True}`，并在只带 usage 的末帧打印。"""
    fake_openai["chunks"] = [
        _chunk("Hel"),
        _chunk("lo"),
        _usage_chunk(_usage(hit=384, miss=195, prompt=579)),
    ]
    client = llm.DeepSeekClient("k", "m", "u")

    out = list(client.chat_stream("你是测试助手。", "u"))

    assert out == ["Hel", "lo"]  # usage 末帧（choices 为空）不影响增量产出
    assert fake_openai["calls"][0]["stream_options"] == {"include_usage": True}
    log = capsys.readouterr().out
    assert "hit=384" in log
    assert "miss=195" in log
    assert "rate=66.3%" in log


def test_chat_stream_without_usage_logs_nothing(fake_openai, capsys) -> None:
    """末帧没有 usage（usage=None）时不应打印任何日志。"""
    fake_openai["chunks"] = [_chunk("a")]
    client = llm.DeepSeekClient("k", "m", "u")

    list(client.chat_stream("s", "u"))

    assert "[llm-usage]" not in capsys.readouterr().out
