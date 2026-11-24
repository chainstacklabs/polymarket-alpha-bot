"""Sale proceeds are what reaches the wallet: the exchange's taking_amount
minus the taker fee.

Live 2026-11-23: 5 NO sold at 0.47 on a sports market (rate 0.05) paid
$2.2877 into the wallet, not the $2.35 the order response reported; 5 YES at
0.49 on a crypto market (rate 0.01) paid $2.4375, not $2.45.
fee = shares * rate * p * (1 - p).
"""

from unittest.mock import MagicMock, create_autospec

import pytest
from polymarket import SecureClient

from core.fees import taker_fee
from core.trading.clob import sell_via_clob

SPORTS = {"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.05}}
CRYPTO = {"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.01}}


def test_taker_fee_matches_live_trades():
    assert taker_fee(5, 0.47, SPORTS) == pytest.approx(2.35 - 2.28773, abs=1e-4)
    assert taker_fee(5, 0.49, CRYPTO) == pytest.approx(2.45 - 2.43751, abs=1e-4)


def test_fee_free_market_pays_nothing():
    assert taker_fee(5, 0.47, {"feesEnabled": False}) == 0.0
    assert taker_fee(5, 0.47, {}) == 0.0


def _client(making: float, taking: float) -> MagicMock:
    client = create_autospec(SecureClient, instance=True)
    client.estimate_market_price.side_effect = RuntimeError("no book")
    client.place_market_order.return_value = MagicMock(
        ok=True, order_id="0xo", making_amount=making, taking_amount=taking
    )
    return client


def test_sale_proceeds_are_net_of_taker_fee():
    sale = sell_via_clob(_client(5, 2.35), "tok", 5, 0.47, fee_market=SPORTS)
    assert sale.proceeds == pytest.approx(2.28773, abs=1e-4)


def test_sale_without_fee_info_keeps_exchange_amount():
    assert sell_via_clob(_client(5, 2.35), "tok", 5, 0.47).proceeds == 2.35
