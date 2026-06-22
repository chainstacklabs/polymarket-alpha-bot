"""Tests for sell_via_clob — sigtype-3 (deposit-wallet) SDK sell path.

`sell_via_clob` now drives a polymarket SecureClient: `place_market_order`
returns an AcceptedOrder (ok=True, order_id, making_amount) or a RejectedOrder
(ok=False, message). making_amount is the shares filled.
"""

from unittest.mock import MagicMock

from core.trading.clob import compute_sell_price, sell_via_clob


def _accepted(order_id: str = "0xabc123", making: float = 5.0) -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.order_id = order_id
    r.making_amount = making
    return r


def _rejected(message: str = "Insufficient liquidity") -> MagicMock:
    r = MagicMock()
    r.ok = False
    r.message = message
    return r


class TestSellViaClob:
    # ---- input validation ----

    def test_zero_amount_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "Invalid sell params" in error
        client.place_market_order.assert_not_called()

    def test_negative_amount_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", -1.0, 0.5)
        assert order_id is None
        assert "Invalid sell params" in error

    def test_zero_price_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0)
        assert order_id is None
        assert "Invalid sell params" in error

    def test_negative_price_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, -0.1)
        assert order_id is None
        assert "Invalid sell params" in error

    # ---- rejection (ok=False) ----

    def test_rejected_order(self):
        client = MagicMock()
        client.place_market_order.return_value = _rejected("Insufficient liquidity")
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "Insufficient liquidity" in error

    def test_exception_returns_error(self):
        client = MagicMock()
        client.place_market_order.side_effect = RuntimeError("boom")
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "boom" in error

    # ---- accepted fills ----

    def test_successful_full_fill(self):
        client = MagicMock()
        client.place_market_order.return_value = _accepted("0xabc123", 5.0)
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id == "0xabc123"
        assert filled == 5.0
        assert error is None

    def test_partial_fill(self):
        client = MagicMock()
        client.place_market_order.return_value = _accepted("0xabc123", 2.5)
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id == "0xabc123"
        assert filled == 2.5
        assert error is None

    # ---- routing: SELL / FAK / min_price ----

    def test_places_sell_fak_with_min_price(self):
        client = MagicMock()
        client.place_market_order.return_value = _accepted()
        sell_via_clob(client, "tok", 5.0, 0.5, slippage=10)

        kwargs = client.place_market_order.call_args.kwargs
        assert kwargs["token_id"] == "tok"
        assert kwargs["side"] == "SELL"
        assert kwargs["shares"] == 5.0
        assert kwargs["order_type"] == "FAK"
        assert kwargs["min_price"] == compute_sell_price(0.5, 10)
