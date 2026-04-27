"""Tests for V2 order struct deltas (#36).

V1 (``py_clob_client.clob_types.MarketOrderArgs``) carries
``fee_rate_bps``/``nonce``/``taker`` as fields on the signed order. V2
(``py_clob_client_v2.clob_types.MarketOrderArgsV2``) drops all three and adds
``metadata`` (bytes32). Taker fees are computed server-side at match time.

These tests assert that the SDKs we ship satisfy the contract, and that
``sell_via_clob`` reaches for the V2 struct under the feature flag.
"""

import importlib
from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest

from core import feature_flags


@pytest.fixture
def v1(monkeypatch):
    monkeypatch.delenv("POLYMARKET_V2_ENABLED", raising=False)
    importlib.reload(feature_flags)


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("POLYMARKET_V2_ENABLED", "true")
    importlib.reload(feature_flags)


class TestOrderStructDeltas:
    """Static field-level deltas between V1 and V2 SDKs."""

    def test_v1_market_order_has_legacy_fee_fields(self):
        from py_clob_client.clob_types import MarketOrderArgs

        names = {f.name for f in fields(MarketOrderArgs)}
        assert "fee_rate_bps" in names
        assert "nonce" in names
        assert "taker" in names

    def test_v2_market_order_drops_legacy_fee_fields(self):
        from py_clob_client_v2.clob_types import MarketOrderArgsV2

        names = {f.name for f in fields(MarketOrderArgsV2)}
        assert "fee_rate_bps" not in names
        assert "nonce" not in names
        assert "taker" not in names

    def test_v2_market_order_adds_metadata(self):
        from py_clob_client_v2.clob_types import MarketOrderArgsV2

        names = {f.name for f in fields(MarketOrderArgsV2)}
        assert "metadata" in names
        # builder_code is the V2 builder-attribution path (was also on V1, kept).
        assert "builder_code" in names

    def test_v1_limit_order_has_legacy_fee_fields(self):
        from py_clob_client.clob_types import OrderArgs

        names = {f.name for f in fields(OrderArgs)}
        assert "fee_rate_bps" in names
        assert "nonce" in names
        assert "taker" in names

    def test_v2_limit_order_drops_legacy_fee_fields(self):
        from py_clob_client_v2.clob_types import OrderArgsV2

        names = {f.name for f in fields(OrderArgsV2)}
        assert "fee_rate_bps" not in names
        assert "nonce" not in names
        assert "taker" not in names
        assert "metadata" in names


class TestSellViaClobRouting:
    """``sell_via_clob`` must build the right struct under each flag."""

    @patch("core.trading.clob._get_filled_size")
    def test_v1_uses_v1_market_order_struct(self, mock_fill, v1):
        from core.trading import clob as clob_mod

        captured: dict = {}

        def fake_create_market_order(args):
            captured["args"] = args
            captured["module"] = type(args).__module__
            return MagicMock()

        client = MagicMock()
        client.create_market_order.side_effect = fake_create_market_order
        client.post_order.return_value = {"success": True, "orderID": "0xabc"}
        client.get_tick_size.return_value = 0.01
        mock_fill.return_value = 5.0

        clob_mod.sell_via_clob(client, "token123", 5.0, 0.5)

        assert captured["module"].startswith("py_clob_client.")
        assert not captured["module"].startswith("py_clob_client_v2.")
        # V1 struct exposes legacy fields (defaults zero/empty).
        assert hasattr(captured["args"], "fee_rate_bps")
        assert hasattr(captured["args"], "nonce")
        assert hasattr(captured["args"], "taker")

    @patch("core.trading.clob._get_filled_size")
    def test_v2_uses_v2_market_order_struct(self, mock_fill, v2):
        from core.trading import clob as clob_mod

        captured: dict = {}

        def fake_create_market_order(args):
            captured["args"] = args
            captured["module"] = type(args).__module__
            return MagicMock()

        client = MagicMock()
        client.create_market_order.side_effect = fake_create_market_order
        client.post_order.return_value = {"success": True, "orderID": "0xabc"}
        client.get_tick_size.return_value = 0.01
        mock_fill.return_value = 5.0

        clob_mod.sell_via_clob(client, "token123", 5.0, 0.5)

        assert captured["module"].startswith("py_clob_client_v2.")
        # Legacy fee fields are gone; metadata is the new addition.
        assert not hasattr(captured["args"], "fee_rate_bps")
        assert not hasattr(captured["args"], "nonce")
        assert not hasattr(captured["args"], "taker")
        assert hasattr(captured["args"], "metadata")
