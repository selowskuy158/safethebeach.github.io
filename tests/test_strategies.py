import pandas as pd
import pytest

from bot.strategies import BreakoutRetest, IctSweep
from bot.strategies.base import in_sessions, mirror, parse_sessions

from .conftest import random_walk


def strategies():
    return [
        BreakoutRetest(ema_trend=50),
        IctSweep(sessions=[], smt=True),
        IctSweep(sessions=[]),
    ]


def run(strategy, df, corr):
    return strategy.find_signals(df, corr if strategy.uses_correlated else None)


@pytest.mark.parametrize("strategy", strategies(), ids=lambda s: f"{s.name}-{s.uses_correlated}")
def test_produces_valid_signals(strategy, pair):
    df, corr = pair
    signals = run(strategy, df, corr)
    assert signals, "expected at least one signal on 6000 random bars"
    for s in signals:
        assert s.side in ("buy", "sell")
        if s.side == "buy":
            assert s.sl < s.entry < s.tp
        else:
            assert s.tp < s.entry < s.sl
        assert abs(s.tp - s.entry) == pytest.approx(strategy.rr * abs(s.entry - s.sl))
        assert s.entry == df["close"].iat[s.index]


@pytest.mark.parametrize("strategy", strategies(), ids=lambda s: f"{s.name}-{s.uses_correlated}")
def test_no_lookahead(strategy, pair):
    """A signal at bar i must be identical whether or not later bars exist."""
    df, corr = pair
    full = run(strategy, df, corr)
    for cut in (1500, 3000, 4500):
        part = run(strategy, df.iloc[:cut], corr.iloc[:cut])
        expected = [(s.index, s.side, s.sl) for s in full if s.index < cut]
        assert [(s.index, s.side, s.sl) for s in part] == expected


@pytest.mark.parametrize("strategy", strategies(), ids=lambda s: f"{s.name}-{s.uses_correlated}")
def test_long_short_symmetry(strategy, pair):
    """Flipping the chart upside down turns every buy into a sell at the mirrored prices."""
    df, corr = pair
    normal = run(strategy, df, corr)
    flipped = run(strategy, mirror(df), mirror(corr))
    assert len(normal) == len(flipped)
    for a, b in zip(normal, flipped):
        assert a.index == b.index and a.side != b.side
        assert a.sl == pytest.approx(-b.sl) and a.entry == pytest.approx(-b.entry)


def test_breakout_retest_textbook_long():
    """Range -> breakout -> impulse -> pullback to the level -> bullish close = buy."""
    closes = [1.1000 + 0.0002 * (i % 5) for i in range(220)] + \
        [1.1005, 1.1009, 1.1004, 1.1000, 1.0998, 1.1001, 1.1003, 1.1002, 1.1006, 1.1008]
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        rows.append((o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
    rows += [  # (open, high, low, close); swing high / breakout level = 1.1011
        (1.1008, 1.1017, 1.1007, 1.1016),   # breakout close
        (1.1016, 1.1031, 1.1015, 1.1030),
        (1.1030, 1.1046, 1.1029, 1.1045),
        (1.1045, 1.1055, 1.1044, 1.1052),   # impulse high B
        (1.1052, 1.1053, 1.1038, 1.1040),
        (1.1040, 1.1041, 1.1028, 1.1030),
        (1.1030, 1.1031, 1.1020, 1.1022),
        (1.1022, 1.1023, 1.1013, 1.1016),   # retest of the level
        (1.1016, 1.1020, 1.1014, 1.1018),
        (1.1018, 1.1031, 1.1016, 1.1030),   # strong bullish close = entry
    ]
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["time"] = pd.date_range("2024-01-01", periods=len(df), freq="15min")
    strat = BreakoutRetest(ema_trend=0, pivot_left=3, pivot_right=3, fib_min=0.382, fib_max=0.786,
                           stoch_oversold=40)  # this short pullback only dips %K to 38
    signals = strat.find_signals(df)
    assert signals, "expected a buy on the last candle"
    last = signals[-1]
    assert (last.index, last.side) == (len(df) - 1, "buy")
    assert last.sl < 1.1013 and last.entry == 1.1030
    # Same chart without the confirmation candle: no trade yet.
    assert not [s for s in strat.find_signals(df.iloc[:-1]) if s.index >= len(df) - 3]


def test_ict_requires_correlated_data_when_smt_on(pair):
    with pytest.raises(ValueError):
        IctSweep(smt=True).find_signals(pair[0])
    assert IctSweep().find_signals(pair[0]) is not None   # SMT is off by default


def test_sessions():
    sessions = parse_sessions(["07:00-10:00", "22:00-01:00"])
    assert in_sessions(pd.Timestamp("2024-01-01 09:00"), sessions, 0)
    assert not in_sessions(pd.Timestamp("2024-01-01 11:00"), sessions, 0)
    assert in_sessions(pd.Timestamp("2024-01-01 00:30"), sessions, 0)       # wraps midnight
    assert in_sessions(pd.Timestamp("2024-01-01 11:00"), sessions, 2)       # server UTC+2 -> 09:00 UTC
    assert in_sessions(pd.Timestamp("2024-01-01 11:00"), [], 0)


def test_ict_session_filter_reduces_signals(pair):
    df, corr = pair
    everywhere = IctSweep(sessions=[]).find_signals(df)
    london = IctSweep(sessions=["07:00-10:00"]).find_signals(df)
    assert len(london) < len(everywhere)
    assert all(7 <= s.time.hour < 10 for s in london)
