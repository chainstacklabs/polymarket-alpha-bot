"""Flow tests for the deposit-wallet (sigtype-3) path.

Mocks the polymarket SecureClient so the production wiring (split / sell /
merge / deploy / approvals) is exercised without network or real funds.
"""

import asyncio
from unittest.mock import MagicMock, patch

from core.trading.executor import TradingExecutor, MarketInfo
from core.wallet.manager import WalletManager


def _accepted_order(making: float = 10.0, order_id: str = "0xorder") -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.order_id = order_id
    r.making_amount = making
    return r


def _market(condition_id="0xcond", yes="111", no="222") -> MarketInfo:
    return MarketInfo(
        market_id="m1",
        question="Will it?",
        condition_id=condition_id,
        yes_token_id=yes,
        no_token_id=no,
        yes_price=0.5,
        no_price=0.5,
    )


def _client_for_trading() -> MagicMock:
    client = MagicMock()
    client.split_position.return_value.wait.return_value.transaction_hash = "0xsplit"
    client.merge_positions.return_value.wait.return_value.transaction_hash = "0xmerge"
    client.place_market_order.return_value = _accepted_order()
    client.is_gasless_ready.return_value = True
    client.wallet = "0xDeposit"
    return client


# ── entry: split + sigtype-3 sell of the unwanted side ───────────────────


def test_buy_single_position_splits_then_sells_unwanted():
    wallet = MagicMock(spec=WalletManager)
    wallet.is_unlocked = True
    ex = TradingExecutor(wallet)
    ex.get_market_info = MagicMock(return_value=_async(_market()))

    client = _client_for_trading()
    result = asyncio.run(
        ex.buy_single_position("m1", "YES", amount=10.0, client=client)
    )

    # Split via SDK with base-unit amount, on the market's condition_id
    client.split_position.assert_called_once()
    kwargs = client.split_position.call_args.kwargs
    assert kwargs["condition_id"] == "0xcond"
    assert kwargs["amount"] == int(10.0 * 1e6)

    # Unwanted side (NO for a YES buy) sold via sigtype-3 market order
    client.place_market_order.assert_called_once()
    sell_kwargs = client.place_market_order.call_args.kwargs
    assert sell_kwargs["token_id"] == "222"
    assert sell_kwargs["side"] == "SELL"
    assert sell_kwargs["order_type"] == "FAK"

    assert result.success is True
    assert result.split_tx == "0xsplit"
    assert result.clob_filled is True
    assert result.wanted_token_id == "111"


def test_buy_skips_sell_below_min_order():
    wallet = MagicMock(spec=WalletManager)
    wallet.is_unlocked = True
    ex = TradingExecutor(wallet)
    # Unwanted value = 1.0 * 0.5 = $0.50 < $1 minimum → sell skipped
    ex.get_market_info = MagicMock(return_value=_async(_market()))

    client = _client_for_trading()
    result = asyncio.run(ex.buy_single_position("m1", "YES", amount=1.0, client=client))

    client.split_position.assert_called_once()
    client.place_market_order.assert_not_called()
    assert result.success is True
    assert "below" in (result.error or "")


# ── exit: merge via SDK relayer batch ────────────────────────────────────


def test_merge_tokens_via_sdk():
    from core.positions.manager import PositionManager

    wallet = MagicMock(spec=WalletManager)
    pm = PositionManager(wallet, MagicMock(), MagicMock())

    client = _client_for_trading()
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        tx_hash, error = pm._merge_tokens("0xcond", 5.0)

    client.merge_positions.assert_called_once()
    kwargs = client.merge_positions.call_args.kwargs
    assert kwargs["condition_id"] == "0xcond"
    assert kwargs["amount"] == int(5.0 * 1e6)
    assert tx_hash == "0xmerge"
    assert error is None


# ── setup: deploy deposit wallet + approvals ─────────────────────────────


def _unlocked_manager() -> WalletManager:
    storage = MagicMock()
    storage.load.return_value = {"address": "0xEOA"}
    wallet = WalletManager(storage, "http://rpc")
    wallet._unlocked_key = "0xkey"
    wallet._relayer_key = "rk"
    return wallet


def test_deploy_deposit_wallet_persists_and_tries_approvals():
    wallet = _unlocked_manager()
    client = _client_for_trading()
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        deposit = wallet.deploy_deposit_wallet()

    # create() auto-deploys; approvals are attempted best-effort
    client.setup_trading_approvals.assert_called_once()
    wallet.storage.set_deposit_wallet.assert_called_once_with("0xDeposit")
    assert deposit == "0xDeposit"


def test_deploy_tolerates_relayer_blocked_approvals():
    """Relayer rejecting an approval operator must not break onboarding."""
    wallet = _unlocked_manager()
    client = _client_for_trading()
    client.setup_trading_approvals.side_effect = RuntimeError(
        "setApprovalForAll operator 0xF3cF... is not in the allowed list"
    )
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        deposit = wallet.deploy_deposit_wallet()  # must NOT raise

    wallet.storage.set_deposit_wallet.assert_called_once_with("0xDeposit")
    assert deposit == "0xDeposit"


def _async(value):
    """Wrap a value in a completed coroutine for AsyncMock-free patching."""

    async def _coro():
        return value

    return _coro()
