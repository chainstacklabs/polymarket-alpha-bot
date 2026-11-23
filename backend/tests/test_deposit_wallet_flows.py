"""Flow tests for the deposit-wallet (sigtype-3) path.

Mocks the polymarket SecureClient so the production wiring (split / sell /
merge / deploy / approvals) is exercised without network or real funds.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from polymarket import SecureClient

from core.trading.executor import MarketInfo, TradingExecutor
from core.wallet.manager import WalletManager


def _accepted_order(
    making: float = 10.0, order_id: str = "0xorder", taking: float = 4.8
) -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.order_id = order_id
    r.making_amount = making
    r.taking_amount = taking
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
    # Autospec against the real SDK: renamed/removed kwargs fail the tests.
    client = create_autospec(SecureClient, instance=True)
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
    assert sell_kwargs["asset_id"] == "222"
    assert sell_kwargs["side"] == "SELL"
    assert sell_kwargs["order_type"] == "FAK"

    assert result.success is True
    assert result.split_tx == "0xsplit"
    assert result.clob_filled is True
    assert result.clob_proceeds == 4.8  # what the exchange paid, for the P&L cost
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


def _buy(amount: float, market: MarketInfo):
    wallet = MagicMock(spec=WalletManager)
    wallet.is_unlocked = True
    ex = TradingExecutor(wallet)
    ex.get_market_info = MagicMock(return_value=_async(market))
    client = _client_for_trading()
    result = asyncio.run(
        ex.buy_single_position("m1", "YES", amount=amount, client=client)
    )
    return client, result


def test_buy_skips_sell_below_market_share_minimum():
    # $3 split = 3 shares of NO, worth $1.50 (passes $1) but < 5-share minimum
    client, result = _buy(3.0, _market())
    client.place_market_order.assert_not_called()
    assert "5-share minimum" in (result.error or "")


def test_buy_skips_sell_below_dollar_minimum():
    # 6 shares (>= 5) but NO at 0.1 → $0.60 < $1
    market = MarketInfo(**{**_market().__dict__, "yes_price": 0.9, "no_price": 0.1})
    client, result = _buy(6.0, market)
    client.place_market_order.assert_not_called()
    assert "$1.0 CLOB minimum" in (result.error or "")


def test_market_share_minimum_read_from_gamma():
    ex = TradingExecutor(MagicMock())
    data = {
        "version": "v1",
        "clobTokenIds": '["1", "2"]',
        "outcomePrices": '["0.5", "0.5"]',
        "orderMinSize": 15,
    }
    with patch(
        "core.trading.executor.fetch_json_with_retry", AsyncMock(return_value=data)
    ):
        info = asyncio.run(ex.get_market_info("m1"))
    assert info.min_order_shares == 15.0


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


def _missing_approvals(n_erc20: int, n_erc1155: int) -> MagicMock:
    state = MagicMock()
    state.missing.erc20 = tuple(
        MagicMock(token_address=f"0x{i:040x}", spender=f"0x{i + 1:040x}", amount=1)
        for i in range(n_erc20)
    )
    state.missing.erc1155 = tuple(
        MagicMock(token_address=f"0x{i:040x}", operator=f"0x{i + 1:040x}")
        for i in range(n_erc1155)
    )
    return state


def test_deploy_deposit_wallet_persists_and_sets_approvals():
    wallet = _unlocked_manager()
    client = _client_for_trading()
    client.get_trading_approvals_state.return_value = _missing_approvals(1, 1)
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        deposit = wallet.deploy_deposit_wallet()

    client.execute_transaction.assert_called_once()
    client.setup_trading_approvals.assert_not_called()
    wallet.storage.set_deposit_wallet.assert_called_once_with("0xDeposit")
    assert deposit == "0xDeposit"


def test_approvals_sent_in_batches_of_four():
    """The relayer rejects all 17 approvals in one batch (Cloudflare 400)."""
    client = _client_for_trading()
    client.get_trading_approvals_state.return_value = _missing_approvals(9, 8)

    assert WalletManager._set_approvals(client) == 17

    sizes = [len(c.kwargs["calls"]) for c in client.execute_transaction.call_args_list]
    assert sizes == [4, 4, 4, 4, 1]
    assert client.execute_transaction.return_value.wait.call_count == 5


def test_no_approval_transaction_when_fully_approved():
    client = _client_for_trading()
    client.get_trading_approvals_state.return_value = _missing_approvals(0, 0)

    assert WalletManager._set_approvals(client) == 0
    client.execute_transaction.assert_not_called()


def test_deploy_tolerates_failed_approvals():
    """A failed approval batch must not undo onboarding: the wallet stays saved."""
    wallet = _unlocked_manager()
    client = _client_for_trading()
    client.get_trading_approvals_state.return_value = _missing_approvals(4, 0)
    client.execute_transaction.side_effect = RuntimeError("blocked by Cloudflare")
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        deposit = wallet.deploy_deposit_wallet()  # must NOT raise

    wallet.storage.set_deposit_wallet.assert_called_once_with("0xDeposit")
    assert deposit == "0xDeposit"


def test_set_approvals_surfaces_relayer_errors():
    """The explicit Approve action reports failures instead of hiding them."""
    wallet = _unlocked_manager()
    client = _client_for_trading()
    client.get_trading_approvals_state.return_value = _missing_approvals(4, 0)
    client.execute_transaction.side_effect = RuntimeError("blocked by Cloudflare")
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        with pytest.raises(RuntimeError, match="Cloudflare"):
            wallet.set_approvals()


def _async(value):
    """Wrap a value in a completed coroutine for AsyncMock-free patching."""

    async def _coro():
        return value

    return _coro()


# ── approval status: delegated to the SDK's required set ──────────────────


def _manager_with_deposit(deposit: str | None) -> WalletManager:
    storage = MagicMock()
    storage.load.return_value = {"address": "0xEOA", "deposit_wallet_address": deposit}
    return WalletManager(storage, "http://rpc")


def _patched_public_client(state=None, error: Exception | None = None):
    client = MagicMock()
    if error:
        client.get_trading_approvals_state.side_effect = error
    else:
        client.get_trading_approvals_state.return_value = state
    cls = MagicMock()
    cls.return_value.__enter__.return_value = client
    return patch("polymarket.PublicClient", cls), client


def test_check_approvals_uses_sdk_state_for_deposit_wallet():
    wallet = _manager_with_deposit("0xDeposit")
    ctx, client = _patched_public_client(state=MagicMock(is_fully_approved=True))
    with ctx:
        assert wallet.check_approvals() is True
    client.get_trading_approvals_state.assert_called_once_with(wallet="0xDeposit")


def test_check_approvals_false_when_sdk_reports_missing():
    wallet = _manager_with_deposit("0xDeposit")
    ctx, _ = _patched_public_client(state=MagicMock(is_fully_approved=False))
    with ctx:
        assert wallet.check_approvals() is False


def test_check_approvals_false_on_lookup_error_or_no_deposit_wallet():
    ctx, _ = _patched_public_client(error=RuntimeError("data api down"))
    with ctx:
        assert _manager_with_deposit("0xDeposit").check_approvals() is False
        assert _manager_with_deposit(None).check_approvals() is False


def test_sdk_autospec_rejects_unknown_kwargs():
    client = _client_for_trading()
    try:
        client.place_market_order(bogus_kwarg=1, side="SELL")
    except TypeError:
        return
    raise AssertionError("autospec should reject kwargs the SDK doesn't accept")
