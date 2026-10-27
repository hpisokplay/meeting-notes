"""anthropic_api：金鑰檔讀取與 Claude API 呼叫（假 client，不連網）。"""
from types import SimpleNamespace

import pytest

from server import anthropic_api
from server.anthropic_api import ApiError, api_enabled, call_api, read_key

FAKE_KEY = "sk-test-123"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    p = tmp_path / "anthropic.key"
    monkeypatch.setenv("MEETINGS_ANTHROPIC_KEY_FILE", str(p))
    return p


def test_read_key_strips_whitespace_and_bom(keyfile):
    keyfile.write_bytes(b"\xef\xbb\xbf  " + FAKE_KEY.encode() + b" \r\n\n")
    assert read_key() == FAKE_KEY
    assert api_enabled() is True


def test_read_key_missing_file_is_none(keyfile):
    assert read_key() is None
    assert api_enabled() is False


def test_read_key_empty_is_none(keyfile):
    keyfile.write_bytes(b"\xef\xbb\xbf \r\n")
    assert read_key() is None
    assert api_enabled() is False


class FakeStream:
    def __init__(self, msg, raise_exc=None):
        self.msg, self.raise_exc = msg, raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        if self.raise_exc:
            raise self.raise_exc
        return self.msg


class FakeClient:
    def __init__(self, msg=None, raise_exc=None):
        self.kwargs = None
        self.messages = SimpleNamespace(stream=self._stream)
        self._msg, self._exc = msg, raise_exc

    def _stream(self, **kw):
        self.kwargs = kw
        return FakeStream(self._msg, self._exc)


def _msg(content, stop="tool_use"):
    return SimpleNamespace(stop_reason=stop, content=content)


def _tool(inp):
    return SimpleNamespace(type="tool_use", name="submit_report", input=inp)


def test_call_api_returns_tool_input():
    c = FakeClient(_msg([SimpleNamespace(type="text", text="x"), _tool({"a": "b"})]))
    assert call_api("sys", "usr", SCHEMA, client=c) == {"a": "b"}
    kw = c.kwargs
    assert kw["model"] == anthropic_api.REPORT_API_MODEL == "claude-opus-5-5"
    assert kw["max_tokens"] == 32000
    assert kw["system"] == "sys"
    assert kw["messages"] == [{"role": "user", "content": "usr"}]
    assert len(kw["tools"]) == 1 and kw["tools"][0]["input_schema"] == SCHEMA
    assert kw["tool_choice"] == {"type": "tool", "name": kw["tools"][0]["name"]}


def test_call_api_truncated_raises():
    c = FakeClient(_msg([_tool({"a": "b"})], stop="max_tokens"))
    with pytest.raises(ApiError):
        call_api("s", "u", SCHEMA, client=c)


def test_call_api_no_tool_block_raises():
    c = FakeClient(_msg([SimpleNamespace(type="text", text="hi")]))
    with pytest.raises(ApiError):
        call_api("s", "u", SCHEMA, client=c)


def test_call_api_non_dict_raises():
    c = FakeClient(_msg([_tool("not a dict")]))
    with pytest.raises(ApiError):
        call_api("s", "u", SCHEMA, client=c)


def test_call_api_wraps_sdk_error_without_leaking_key():
    class StatusErr(Exception):
        status_code = 529

    c = FakeClient(raise_exc=StatusErr(f"bad key {FAKE_KEY} body=逐字稿內容"))
    with pytest.raises(ApiError) as ei:
        call_api("s", "u", SCHEMA, client=c)
    text = str(ei.value)
    assert FAKE_KEY not in text and "逐字稿內容" not in text
    assert "StatusErr" in text and "529" in text
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
