"""Drive the live Runner against a fake MT5 broker."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from bot.broker import Account, Position, SymbolSpec
from bot.config import prop_rules_from_config
from bot.news import NewsFilter
from bot.risk import PropGuard
from bot.run import Runner
from bot.strategies import Signal

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config" / "config.example.yaml").read_text())


class FakeBroker:
    magic = 150820

    def __init__(self, equity=2000.0, positions=None):
        self.acct = Account(balance=2000.0, equity=equity, currency="USD")
        self._positions = positions or []
        self.orders, self.closed = [], []

    def rates(self, symbol, timeframe, count):
        t = pd.date_range("2024-01-01", periods=count, freq="15min")
        return pd.DataFrame({"time": t, "open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1})

    def account(self):
        return self.acct

    def symbol_spec(self, symbol):
        return SymbolSpec(symbol, 5, 0.00001, 100_000, 0.01, 100, 0.01, 0)

    def quote(self, symbol):
        return 1.1000, 1.10005

    def server_time(self, symbol):
        return pd.Timestamp("2024-01-02 09:00")

    def positions(self, symbol=None, own_only=True):
        return [p for p in self._positions if not own_only or p.magic == self.magic]

    def market_order(self, symbol, side, volume, sl, tp, comment):
        self.orders.append((symbol, side, volume, sl, tp))
        return 1

    def close_position(self, position):
        self.closed.append(position)


class OneSignal:
    """Strategy stub: a buy on the newest candle with a 20 pip stop."""
    name, rr, uses_correlated = "stub", 3.0, False

    def find_signals(self, df, correlated=None):
        i = len(df) - 1
        return [Signal(i, df["time"].iat[i], "buy", 1.1, 1.0980, 1.1060, "stub")]


def make_runner(broker, mode="live", **prop):
    cfg = dict(CONFIG, mode=mode, symbols=["EURUSD"], prop=CONFIG["prop"] | prop)
    guard = PropGuard(prop_rules_from_config(cfg))
    return Runner(cfg, broker, OneSignal(), guard, NewsFilter(None))


def test_live_order_sizes_risk_and_rebases_target():
    broker = FakeBroker()
    make_runner(broker).step()
    assert len(broker.orders) == 1
    symbol, side, lots, sl, tp = broker.orders[0]
    # ask 1.10005, stop 1.0980 -> 20.5 pips; $5 risk -> 0.024 -> 0.02 lots
    assert (symbol, side, lots, sl) == ("EURUSD", "buy", 0.02, 1.0980)
    assert tp == pytest.approx(1.10005 + 3 * (1.10005 - 1.0980))


def test_paper_mode_sends_nothing():
    broker = FakeBroker()
    make_runner(broker, mode="paper").step()
    assert broker.orders == []


def test_signal_acted_on_once_per_candle():
    broker = FakeBroker()
    runner = make_runner(broker)
    runner.step()
    runner.step()
    assert len(broker.orders) == 1


def test_manual_trade_without_stop_blocks_bot():
    manual = Position(7, "GBPUSD", "buy", 0.1, 1.27, 0.0, 0.0, 0.0, magic=0)
    broker = FakeBroker(positions=[manual])
    make_runner(broker).step()
    assert broker.orders == []


def test_near_trailing_floor_flattens_and_stops():
    own = Position(8, "GBPUSD", "buy", 0.02, 1.27, 1.26, 1.30, -80.0, magic=FakeBroker.magic)
    broker = FakeBroker(equity=1900.0, positions=[own])   # floor 1880, guard level 1904
    make_runner(broker).step()
    assert broker.orders == [] and broker.closed == [own]


def test_ea_size_refused_for_large_accounts():
    with pytest.raises(RuntimeError):
        make_runner(FakeBroker(), initial_balance=50_000).guard.check_ea_allowed()
