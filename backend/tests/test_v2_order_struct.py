"""Tests for V2 order struct contract.

``py_clob_client_v2.clob_types.MarketOrderArgsV2`` has no
``fee_rate_bps``/``nonce``/``taker`` fields (taker fees are server-side at
match time) and adds ``metadata`` (bytes32). These tests assert the SDK we
ship satisfies the contract, and that ``sell_via_clob`` reaches for the V2
struct.
"""

from dataclasses import fields
from unittest.mock import MagicMock, patch


class TestOrderStructDeltas:
    """Static field-level assertions for the V2 SDK."""

    def test_market_order_drops_legacy_fee_fields(self):
        from py_clob_client_v2.clob_types import MarketOrderArgsV2

        names = {f.name for f in fields(MarketOrderArgsV2)}
        assert "fee_rate_bps" not in names
        assert "nonce" not in names
        assert "taker" not in names

    def test_market_order_adds_metadata(self):
        from py_clob_client_v2.clob_types import MarketOrderArgsV2

        names = {f.name for f in fields(MarketOrderArgsV2)}
        assert "metadata" in names
        assert "builder_code" in names

    def test_limit_order_drops_legacy_fee_fields(self):
        from py_clob_client_v2.clob_types import OrderArgsV2

        names = {f.name for f in fields(OrderArgsV2)}
        assert "fee_rate_bps" not in names
        assert "nonce" not in names
        assert "taker" not in names
        assert "metadata" in names


class TestSellViaClobRouting:
    @patch("core.trading.clob._get_filled_size")
    def test_uses_v2_market_order_struct(self, mock_fill):
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
        assert not hasattr(captured["args"], "fee_rate_bps")
        assert not hasattr(captured["args"], "nonce")
        assert not hasattr(captured["args"], "taker")
        assert hasattr(captured["args"], "metadata")
