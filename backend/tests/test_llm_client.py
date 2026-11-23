"""Empty model replies: retried, then a clear error (not a None result).

Live 2026-11-23 (deepseek/deepseek-v4.1-flash): a reply with `content: null`
came back as None and broke the caller ("'NoneType' object has no attribute
'strip'" in polyclaw, which shares this client).
"""

import asyncio

import httpx
import pytest

from core.models import LLM_MAX_RETRIES, LLMClient


def _llm(replies: list[dict]) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=replies.pop(0))

    llm = LLMClient(model="m", api_key="k")
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


def _reply(content, finish="stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


def test_empty_reply_is_retried():
    llm = _llm([_reply(None, "length"), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_always_empty_raises_a_clear_error():
    llm = _llm([_reply(None, "length")] * LLM_MAX_RETRIES)
    with pytest.raises(RuntimeError, match="no text.*finish_reason=length"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
