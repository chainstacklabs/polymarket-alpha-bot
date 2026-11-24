"""Tests for sell_via_clob — sigtype-3 (deposit-wallet) SDK sell path.

`sell_via_clob` now drives a polymarket SecureClient: `place_market_order`
returns an AcceptedOrder (ok=True, order_id, making_amount, taking_amount) or a
RejectedOrder (ok=False, message). For a SELL, making_amount is the shares
filled and taking_amount the pUSD received.
"""

from decimal import Decimal
from unittest.mock import MagicMock, create_autospec

from polymarket import SecureClient

from core.trading.clob import compute_sell_price, sell_via_clob


def _client(book_price: str = "0.5") -> MagicMock:
    # Autospec against the real SDK: a renamed/removed kwarg fails the test.
    client = create_autospec(SecureClient, instance=True)
    client.estimate_market_price.return_value = Decimal(book_price)
    return client


def _accepted(
    order_id: str = "0xabc123", making: float = 5.0, taking: float = 2.4
) -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.order_id = order_id
    r.making_amount = making
    r.taking_amount = taking
    return r


def _rejected(message: str = "Insufficient liquidity") -> MagicMock:
    r = MagicMock()
    r.ok = False
    r.message = message
    return r


class TestSellViaClob:
    # ---- input validation ----

    def test_zero_amount_returns_error(self):
        client = _client()
        r = sell_via_clob(client, "token123", 0, 0.5)
        assert r.order_id is None
        assert r.filled == 0.0
        assert "Invalid sell params" in r.error
        client.place_market_order.assert_not_called()

    def test_negative_amount_returns_error(self):
        client = _client()
        r = sell_via_clob(client, "token123", -1.0, 0.5)
        assert r.order_id is None
        assert "Invalid sell params" in r.error

    def test_zero_price_returns_error(self):
        client = _client()
        r = sell_via_clob(client, "token123", 5.0, 0)
        assert r.order_id is None
        assert "Invalid sell params" in r.error

    def test_negative_price_returns_error(self):
        client = _client()
        r = sell_via_clob(client, "token123", 5.0, -0.1)
        assert r.order_id is None
        assert "Invalid sell params" in r.error

    # ---- rejection (ok=False) ----

    def test_rejected_order(self):
        client = _client()
        client.place_market_order.return_value = _rejected("Insufficient liquidity")
        r = sell_via_clob(client, "token123", 5.0, 0.5)
        assert r.order_id is None
        assert r.filled == 0.0
        assert "Insufficient liquidity" in r.error

    def test_exception_returns_error(self):
        client = _client()
        client.place_market_order.side_effect = RuntimeError("boom")
        r = sell_via_clob(client, "token123", 5.0, 0.5)
        assert r.order_id is None
        assert r.filled == 0.0
        assert "boom" in r.error

    # ---- accepted fills ----

    def test_successful_full_fill(self):
        client = _client()
        client.place_market_order.return_value = _accepted("0xabc123", 5.0)
        r = sell_via_clob(client, "token123", 5.0, 0.5)
        assert r.order_id == "0xabc123"
        assert r.filled == 5.0
        assert r.error is None

    def test_reports_exchange_proceeds(self):
        # pUSD received comes from the fill, not shares x quoted price
        client = _client()
        client.place_market_order.return_value = _accepted("0xabc123", 5.0, 2.37)
        r = sell_via_clob(client, "token123", 5.0, 0.5)
        assert r.proceeds == 2.37

    def test_rejection_has_no_proceeds(self):
        client = _client()
        client.place_market_order.return_value = _rejected()
        assert sell_via_clob(client, "token123", 5.0, 0.5).proceeds == 0.0

    def test_partial_fill(self):
        client = _client()
        client.place_market_order.return_value = _accepted("0xabc123", 2.5)
        r = sell_via_clob(client, "token123", 5.0, 0.5)
        assert r.order_id == "0xabc123"
        assert r.filled == 2.5
        assert r.error is None

    # ---- routing: SELL / FAK / min_price ----

    def test_places_sell_fak_with_min_price(self):
        client = _client()
        client.place_market_order.return_value = _accepted()
        sell_via_clob(client, "tok", 5.0, 0.5, slippage=10)

        kwargs = client.place_market_order.call_args.kwargs
        assert kwargs["asset_id"] == "tok"
        assert kwargs["side"] == "SELL"
        assert kwargs["shares"] == 5.0
        assert kwargs["order_type"] == "FAK"
        assert kwargs["min_price"] == compute_sell_price(0.5, 10)

    # ---- floor from the order book ----

    def test_floor_sits_under_the_order_book_price(self):
        # Live 2026-11-23: mid 0.205, best bid 0.16. A floor 10% under the
        # mid (0.18) matched nothing; it has to sit under what the book pays.
        client = _client(book_price="0.16")
        client.place_market_order.return_value = _accepted()
        sell_via_clob(client, "tok", 7.0, 0.205, slippage=10)

        est = client.estimate_market_price.call_args.kwargs
        assert est["asset_id"] == "tok"
        assert est["side"] == "SELL"
        assert est["shares"] == 7.0
        # FOK: a book that can't take all 7 raises instead of quoting its
        # lowest bid (FAK would, which drops the floor to the bottom).
        assert est["order_type"] == "FOK"
        kwargs = client.place_market_order.call_args.kwargs
        assert kwargs["min_price"] == compute_sell_price(0.16, 10)

    def test_floor_falls_back_to_price_when_book_read_fails(self):
        client = _client()
        client.estimate_market_price.side_effect = RuntimeError("no book")
        client.place_market_order.return_value = _accepted()
        sell_via_clob(client, "tok", 5.0, 0.5, slippage=10)
        kwargs = client.place_market_order.call_args.kwargs
        assert kwargs["min_price"] == compute_sell_price(0.5, 10)


def test_sell_floor_never_above_the_book_price():
    # The floor comes from real bids now. Bids under a cent exist only on
    # 0.001-tick books, where a 0.01 floor would never fill.
    assert compute_sell_price(0.70, 10) == 0.63
    assert compute_sell_price(0.005, 10) == 0.004
    assert compute_sell_price(0.0005, 10) == 0.001  # lowest tick
    # Rounded down, not to the nearest cent: 0.029 * 0.9 = 0.0261 -> 0.02.
    assert compute_sell_price(0.029, 10) == 0.02
