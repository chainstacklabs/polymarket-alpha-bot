"""Market protocol handling: v1 (CTF) and v2 (Protocol V2) both trade.

Gamma tags each market with `version`. Trading IDs come from `clobTokenIds`
for v1 and `positionIds` for v2 (chosen by version even when both are
present; Gamma sends both on v2). Unknown versions never reach the pipeline
or the executor. Shapes copied from live markets on 2026-11-22.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.market_poller import process_event_markets
from core.markets import is_supported_version, outcome_token_ids
from core.steps.fetch import process_events
from core.trading.executor import TradingExecutor

V2_IDS = [
    "463772829896766161280654004693731929026803191631119800585534187027092733952",
    "463772829896766161280654004693731929026803191631119800585534187027092733953",
]


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
    if version == "v2":
        m["positionIds"] = V2_IDS
    return m


def _event(*markets: dict) -> dict:
    return {"id": "e1", "active": True, "closed": False, "markets": list(markets)}


class TestVersions:
    def test_v1_and_v2_supported(self):
        assert is_supported_version({"version": "v1"})
        assert is_supported_version({"version": "v2"})

    def test_missing_or_unknown_rejected(self):
        assert not is_supported_version({})
        assert not is_supported_version({"version": "v3"})

    def test_v2_uses_position_ids_not_clob_ids(self):
        assert outcome_token_ids(_market("m", "v2")) == V2_IDS

    def test_v1_uses_clob_token_ids(self):
        assert outcome_token_ids(_market("m", "v1")) == ["1", "2"]

    def test_unknown_version_gives_no_ids(self):
        assert outcome_token_ids(_market("m", "v3")) == []
        assert outcome_token_ids(_market("m", None)) == []


class TestPipelineFetch:
    def test_keeps_v1_and_v2_drops_unknown(self):
        events = [_event(_market("a", "v1"), _market("b", "v2"), _market("c", None))]
        out = process_events(events)
        assert [m["id"] for m in out[0]["markets"]] == ["a", "b"]


class TestPoller:
    def test_keeps_v1_and_v2_drops_unknown(self):
        out = process_event_markets(
            _event(_market("a", "v1"), _market("b", "v2"), _market("c", "v9"))
        )
        assert [m["id"] for m in out["markets"]] == ["a", "b"]


class TestExecutor:
    def _run(self, data: dict):
        executor = TradingExecutor(MagicMock())
        with patch(
            "core.trading.executor.fetch_json_with_retry",
            AsyncMock(return_value=data),
        ):
            return asyncio.run(executor.get_market_info("m1"))

    def test_v2_market_uses_position_ids(self):
        info = self._run({**_market("m1", "v2"), "conditionId": "0x01ab"})
        assert (info.yes_token_id, info.no_token_id) == tuple(V2_IDS)
        assert info.protocol == "v2"

    def test_v1_market_uses_clob_ids(self):
        info = self._run({**_market("m1", "v1"), "conditionId": "0xabc"})
        assert (info.yes_token_id, info.no_token_id) == ("1", "2")
        assert info.protocol == "v1"

    def test_refuses_unknown_version(self):
        with pytest.raises(ValueError, match="unsupported protocol"):
            self._run(_market("m1", None))
