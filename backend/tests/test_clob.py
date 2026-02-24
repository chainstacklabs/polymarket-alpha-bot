"""Tests for sell_via_clob — input validation, post_order failure, and fill checking."""

from unittest.mock import MagicMock, patch

from core.trading.clob import sell_via_clob


class TestSellViaClob:
    """Tests for sell_via_clob function."""

    # ---- Bug fix #1: Invalid price/amount guard ----

    def test_zero_amount_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "Invalid sell params" in error
        client.post_order.assert_not_called()

    def test_negative_amount_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", -1.0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "Invalid sell params" in error

    def test_zero_price_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0)
        assert order_id is None
        assert filled == 0.0
        assert "Invalid sell params" in error

    def test_negative_price_returns_error(self):
        client = MagicMock()
        order_id, filled, error = sell_via_clob(client, "token123", 5.0, -0.1)
        assert order_id is None
        assert filled == 0.0
        assert "Invalid sell params" in error

    # ---- Bug fix #2: post_order soft-failure ----

    @patch("core.trading.clob.OrderType", create=True)
    @patch("core.trading.clob.SELL", create=True)
    @patch("core.trading.clob.MarketOrderArgs", create=True)
    def test_post_order_soft_failure(self, mock_args, mock_sell, mock_ot):
        """post_order returns success=false on HTTP 200 — should return error."""
        client = MagicMock()
        client.post_order.return_value = {
            "success": False,
            "errorMsg": "Insufficient liquidity",
        }

        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "Insufficient liquidity" in error

    @patch("core.trading.clob.OrderType", create=True)
    @patch("core.trading.clob.SELL", create=True)
    @patch("core.trading.clob.MarketOrderArgs", create=True)
    def test_post_order_soft_failure_no_msg(self, mock_args, mock_sell, mock_ot):
        """post_order returns success=false with no errorMsg."""
        client = MagicMock()
        client.post_order.return_value = {"success": False}

        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id is None
        assert filled == 0.0
        assert "rejected" in error.lower()

    # ---- Successful flow ----

    @patch("core.trading.clob._get_filled_size")
    def test_successful_full_fill(self, mock_fill):
        """Happy path: post succeeds, full fill confirmed."""
        mock_fill.return_value = 5.0
        client = MagicMock()
        client.post_order.return_value = {
            "success": True,
            "orderID": "0xabc123",
        }

        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id == "0xabc123"
        assert filled == 5.0
        assert error is None

    @patch("core.trading.clob._get_filled_size")
    def test_partial_fill(self, mock_fill):
        """FAK partial fill — filled_size < amount."""
        mock_fill.return_value = 2.5
        client = MagicMock()
        client.post_order.return_value = {
            "success": True,
            "orderID": "0xabc123",
        }

        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id == "0xabc123"
        assert filled == 2.5
        assert error is None

    @patch("core.trading.clob._get_filled_size")
    def test_zero_fill(self, mock_fill):
        """FAK order posted but nothing matched."""
        mock_fill.return_value = 0.0
        client = MagicMock()
        client.post_order.return_value = {
            "success": True,
            "orderID": "0xabc123",
        }

        order_id, filled, error = sell_via_clob(client, "token123", 5.0, 0.5)
        assert order_id == "0xabc123"
        assert filled == 0.0
        assert error is None
