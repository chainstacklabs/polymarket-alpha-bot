"""Empty model replies: retried, then a clear error (not a None result).

Live 2026-11-23 (deepseek/deepseek-v4.1-flash): a reply with `content: null`
came back as None and broke the caller ("'NoneType' object has no attribute
'strip'" in polyclaw, which shares this client).
"""

import asyncio

import httpx
import pytest

from core.models import LLM_MAX_RETRIES, LLMClient, LLMEmptyReply


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
    llm = _llm([_reply(None), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_always_empty_raises_a_clear_error():
    llm = _llm([_reply(None)] * LLM_MAX_RETRIES)
    # "LLM" in the text: validation stats count it as an LLM failure.
    with pytest.raises(
        LLMEmptyReply, match="LLM m returned no text.*finish_reason=stop"
    ):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))


def test_out_of_budget_is_not_retried():
    # Same prompt, same budget: a retry would be billed and empty again.
    replies = [_reply(None, "length"), _reply("ok")]
    llm = _llm(replies)
    with pytest.raises(LLMEmptyReply, match="finish_reason=length"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert len(replies) == 1  # one call made


def test_error_body_without_choices_is_retried():
    # OpenRouter can answer HTTP 200 with only an error object.
    llm = _llm([{"error": {"message": "provider down"}}, _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_error_body_reason_is_reported():
    llm = _llm([{"error": {"message": "provider down"}}] * LLM_MAX_RETRIES)
    with pytest.raises(LLMEmptyReply, match="provider down"):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))


def test_whitespace_reply_is_empty():
    llm = _llm([_reply("  \n"), _reply("ok")])
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == "ok"


def test_pipeline_does_not_retry_an_empty_reply_again(monkeypatch):
    # complete() already retried: the per-group loop must not multiply it.
    from unittest.mock import MagicMock

    from core.steps import implications

    calls = []

    class Empty:
        model = "m"

        async def complete(self, *args, **kwargs):
            calls.append(1)
            raise LLMEmptyReply("LLM m returned no text")

    monkeypatch.setattr(implications, "get_llm_client", lambda model=None: Empty())
    state = MagicMock()
    state.get_implication.return_value = None
    group = {"group_id": "g1", "title": "G", "markets": []}
    monkeypatch.setattr(implications, "format_group_for_prompt", lambda g: g["title"])
    asyncio.run(implications.extract_implications_batch([group], [group], state))
    assert len(calls) == 1
