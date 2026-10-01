import math

import pandas as pd
import pytest

from bot.news import NewsFilter, symbol_currencies
from bot.risk import PropGuard, PropRules, position_size

EURUSD_VPU = 100_000  # $ per 1.0 price move per lot


def test_position_size_eurusd():
    # $2,000 x 0.25% = $5 risk, 20 pip stop -> $5 / (0.0020 * 100k) = 0.025 -> 0.02 lots
    assert position_size(2000, 0.25, 1.1000, 1.0980, EURUSD_VPU, 0.01, 100, 0.01) == 0.02


def test_position_size_never_rounds_up_past_risk():
    # 60 pip stop at $5 risk wants 0.0083 lots < 0.01 minimum -> skip the trade
    assert position_size(2000, 0.25, 1.1000, 1.0940, EURUSD_VPU, 0.01, 100, 0.01) == 0.0


def instant(**kw):
    params = dict(initial_balance=2000, daily_loss_pct=0, max_loss_pct=6, trailing_max_loss=True,
                  safety_buffer=0.8, personal_daily_stop_pct=0, risk_limit_pct=3)
    return PropRules(**(params | kw))


def test_trailing_floor_follows_balance_and_caps_at_initial():
    g = PropGuard(instant())
    assert g.max_loss_floor() == 1880
    g.update("2024-01-01", 2050, 2050)
    assert g.max_loss_floor() == 1930
    g.update("2024-01-02", 2300, 2300)
    assert g.max_loss_floor() == 2000           # capped at the initial balance
    g.update("2024-01-03", 2100, 2100)          # losses never lower it
    assert g.max_loss_floor() == 2000


def test_seeded_high_water_matches_fundednext_dashboard():
    # Dashboard trailing_drawdown 1905.79 -> high water 2025.79
    g = PropGuard(instant(high_water_balance=2025.79))
    assert g.max_loss_floor() == pytest.approx(1905.79)


def test_guard_blocks_trades_that_could_eat_the_buffer():
    g = PropGuard(instant())
    g.update("2024-01-01", 2000, 2000)
    # guard level = 1880 + 0.2 * 120 = 1904 -> at most $96 of combined risk
    assert g.can_open(2000, open_risk=40, new_risk=55)[0]
    assert not g.can_open(2000, open_risk=45, new_risk=55)[0]
    assert not g.can_open(1910, open_risk=0, new_risk=10)[0]


def test_firm_risk_limit_per_trade():
    g = PropGuard(instant(safety_buffer=1.0))
    g.update("2024-01-01", 2000, 2000)
    assert not g.can_open(2000, 0, 61)[0]       # 3% of $2,000 = $60


def test_no_daily_limit_on_instant_but_personal_stop_works():
    assert math.isinf(instant().daily_limit)
    g = PropGuard(instant(personal_daily_stop_pct=2))
    g.update("2024-01-01", 2000, 2000)
    assert g.limit_hit(1959) is not None and g.limit_hit(1961) is None
    assert not g.can_open(1970, open_risk=0, new_risk=15)[0]   # 30 lost + 15 > 40
    g.update("2024-01-02", 1970, 1970)                          # new day resets the daily stop
    assert g.can_open(1970, open_risk=0, new_risk=15)[0]


def test_two_step_daily_limit_uses_buffer():
    rules = PropRules(initial_balance=10_000, daily_loss_pct=5, max_loss_pct=10, trailing_max_loss=False)
    assert rules.daily_limit == pytest.approx(400)              # 80% of $500


def test_ea_size_check():
    PropGuard(instant()).check_ea_allowed()
    with pytest.raises(RuntimeError):
        PropGuard(PropRules(initial_balance=50_000)).check_ea_allowed()


def test_guard_state_persists(tmp_path):
    path = tmp_path / "state.json"
    g = PropGuard(instant(), path)
    g.update("2024-01-01", 2100, 2100)
    assert PropGuard(instant(), path).max_loss_floor() == 1980


def test_news_filter(tmp_path):
    f = tmp_path / "news.json"
    f.write_text('[{"time": "2026-10-02T12:30:00Z", "currency": "USD", "impact": "high", "title": "NFP"},'
                 ' {"time": "2026-10-02T09:00:00Z", "currency": "EUR", "impact": "low", "title": "minor"}]')
    nf = NewsFilter(f, 10, 10)
    assert nf.blocked("EURUSD", pd.Timestamp("2026-10-02 12:25")) is not None
    assert nf.blocked("EURUSD", pd.Timestamp("2026-10-02 12:45")) is None
    assert nf.blocked("EURGBP", pd.Timestamp("2026-10-02 12:30")) is None
    assert nf.blocked("EURUSD", pd.Timestamp("2026-10-02 09:00")) is None   # low impact ignored
    assert symbol_currencies("XAUUSD.r") == {"XAU", "USD"}
