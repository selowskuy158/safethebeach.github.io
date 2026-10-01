from pathlib import Path

import yaml

from bot.backtest import monte_carlo, simulate_account, summarize, value_per_price_unit
from bot.config import strategy_from_config

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config" / "config.example.yaml").read_text())


def test_value_per_price_unit():
    assert value_per_price_unit("EURUSD", 1.1, 100_000) == 100_000
    assert value_per_price_unit("USDJPY", 150.0, 100_000) == 100_000 / 150.0


def test_account_simulation_respects_guard(pair):
    eur, gbp = pair
    strategy = strategy_from_config(CONFIG, "ict_sweep", sessions=[], smt=True)
    res = simulate_account({"EURUSD": eur, "GBPUSD": gbp}, strategy, CONFIG, risk_pct=0.25)
    assert res.trades
    assert res.breached_at is None
    for t in res.trades:
        assert t.lots >= 0.01
        assert t.risk_money <= 2000 * 0.25 / 100 * 1.6   # rounding down + commission + slippage only
        if t.reason == "sl":
            assert -1.3 < t.r < -0.9
        if t.reason == "tp":
            assert t.r > 2.5
    s = summarize(res, 8)
    assert s["trades"] == len(res.trades)


def test_high_risk_gets_stopped_by_guard_not_breached(pair):
    eur, gbp = pair
    strategy = strategy_from_config(CONFIG, "ict_sweep", sessions=[])
    res = simulate_account({"EURUSD": eur, "GBPUSD": gbp}, strategy, CONFIG, risk_pct=2.0)
    assert res.breached_at is None
    assert res.blocked["guard"] + res.blocked["halted"] > 0


def test_monte_carlo_obvious_cases():
    winner = monte_carlo([3.0, -1.0, -1.0], [1.0], 6, True, 8, 0.8, sims=500)
    loser = monte_carlo([-1.0, -1.0, 0.5], [1.0], 6, True, 8, 0.8, sims=500)
    assert winner.loc["P(+8% first)", "1%"] > loser.loc["P(+8% first)", "1%"]
    assert loser.loc["P(max loss first)", "1%"] == "100%"
