"""Tests for V2 collateral plumbing — USDC.e <-> pUSD switch under POLYMARKET_V2_ENABLED."""

import importlib

import pytest

from core import feature_flags
from core.positions import manager as positions_manager
from core.trading import executor as trading_executor
from core.wallet import manager as wallet_manager
from core.wallet.contracts import CONTRACTS, V2_CONTRACTS


@pytest.fixture
def v1(monkeypatch):
    monkeypatch.delenv("POLYMARKET_V2_ENABLED", raising=False)
    importlib.reload(feature_flags)


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("POLYMARKET_V2_ENABLED", "true")
    importlib.reload(feature_flags)


class TestFeatureFlag:
    def test_default_is_false(self, v1):
        assert feature_flags.v2_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "yes", "on"])
    def test_truthy_values(self, monkeypatch, val):
        monkeypatch.setenv("POLYMARKET_V2_ENABLED", val)
        assert feature_flags.v2_enabled() is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "anything"])
    def test_falsy_values(self, monkeypatch, val):
        monkeypatch.setenv("POLYMARKET_V2_ENABLED", val)
        assert feature_flags.v2_enabled() is False


class TestCollateralRouting:
    def test_v1_uses_usdc_e_everywhere(self, v1):
        assert wallet_manager._collateral_address() == CONTRACTS["USDC_E"]
        assert trading_executor._collateral_address() == CONTRACTS["USDC_E"]
        assert positions_manager._collateral_address() == CONTRACTS["USDC_E"]

    def test_v2_uses_pusd_everywhere(self, v2):
        assert wallet_manager._collateral_address() == V2_CONTRACTS["PUSD"]
        assert trading_executor._collateral_address() == V2_CONTRACTS["PUSD"]
        assert positions_manager._collateral_address() == V2_CONTRACTS["PUSD"]


class TestExchangeRouting:
    def test_v1_targets_v1_exchanges(self, v1):
        ctf, neg = wallet_manager._exchange_addresses()
        assert ctf == CONTRACTS["CTF_EXCHANGE"]
        assert neg == CONTRACTS["NEG_RISK_CTF_EXCHANGE"]

    def test_v2_targets_v2_exchanges(self, v2):
        ctf, neg = wallet_manager._exchange_addresses()
        assert ctf == V2_CONTRACTS["CTF_EXCHANGE_V2"]
        assert neg == V2_CONTRACTS["NEG_RISK_CTF_EXCHANGE_V2"]
