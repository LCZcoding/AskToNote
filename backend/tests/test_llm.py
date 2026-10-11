"""T1.2 LLM 抽象层单测：验证 chat / chat_stream 行为与传给 OpenAI SDK 的参数。

全部用例通过 monkeypatch 把 `app.llm.OpenAI` 换成假客户端，**不发起真实网络请求**
（不产生计费）。真实调用的验证见 Tasks.md T1.2 的文档验证步骤（Plan §5）。
"""
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

import app.llm as llm


def _chunk(text):
    """构造一个流式 chunk：choices[0].delta.content = text。"""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _completion(content):
    """构造一个非流式响应：choices[0].message.content = content。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


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
            return _completion(state["content"])

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
