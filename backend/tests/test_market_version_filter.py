"""Protocol V2 markets must never reach the pipeline or the trade executor.

Gamma tags each market with `version`: "v1" (CTF, clobTokenIds) or "v2"
(Protocol V2, positionIds + ExchangeV3). The bot only trades v1; reading
clobTokenIds on a v2 market would sign orders for the wrong token.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.market_poller import process_event_markets
from core.markets import is_supported_version
from core.steps.fetch import process_events
from core.trading.executor import TradingExecutor


def _market(mid: str, version: str | None) -> dict:
    m = {
        "id": mid,
        "active": True,
        "closed": False,
        "clobTokenIds": '["1", "2"]',
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.4", "0.6"]',
    }
    if version is not None:
        m["version"] = version
    return m


def _event(*markets: dict) -> dict:
    return {"id": "e1", "active": True, "closed": False, "markets": list(markets)}


class TestIsSupportedVersion:
    def test_v1_supported(self):
        assert is_supported_version({"version": "v1"})

    def test_v2_rejected(self):
        assert not is_supported_version({"version": "v2"})

    def test_missing_rejected(self):
        assert not is_supported_version({})

    def test_unknown_rejected(self):
        assert not is_supported_version({"version": "v3"})


class TestPipelineFetch:
    def test_keeps_only_v1_markets(self):
        events = [_event(_market("a", "v1"), _market("b", "v2"), _market("c", None))]
        out = process_events(events)
        assert [m["id"] for m in out[0]["markets"]] == ["a"]
        # JSON fields still parsed for kept markets
        assert out[0]["markets"][0]["clobTokenIds"] == ["1", "2"]


class TestPoller:
    def test_keeps_only_v1_markets(self):
        out = process_event_markets(_event(_market("a", "v1"), _market("b", "v2")))
        assert [m["id"] for m in out["markets"]] == ["a"]


class TestExecutor:
    def _run(self, data: dict):
        executor = TradingExecutor(MagicMock())
        with patch(
            "core.trading.executor.fetch_json_with_retry",
            AsyncMock(return_value=data),
        ):
            return asyncio.run(executor.get_market_info("m1"))

    def test_refuses_v2_market(self):
        with pytest.raises(ValueError, match="only CTF"):
            self._run(_market("m1", "v2"))

    def test_accepts_v1_market(self):
        info = self._run({**_market("m1", "v1"), "conditionId": "0xabc"})
        assert info.yes_token_id == "1"
        assert info.no_token_id == "2"
