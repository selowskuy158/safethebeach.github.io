# FundedNext MT5 Forex Bot

An automated forex bot for a **FundedNext Stellar Instant** account on **MetaTrader 5**,
with two strategies you can compare, a risk guard built around FundedNext's rules, an
account-level backtester, and matching **TradingView** Pine scripts.

> **Status: unproven.** Nothing here has been backtested on real market data yet.
> Run the backtest on several years of real data and paper-trade before going live.

## How the pieces fit

| Piece | Role |
|---|---|
| **MetaTrader 5** (`bot/broker/mt5_broker.py`) | Price data and order execution. FundedNext MCP cannot place trades. |
| **Strategies** (`bot/strategies/`) | `breakout_retest` and `ict_sweep`. Pure functions of closed candles, so live = backtest. |
| **Risk guard** (`bot/risk.py`) | Position sizing + FundedNext rules: trailing max loss, risk limit, personal daily stop. |
| **Backtester** (`bot/backtest.py`) | Simulates the $2,000 account trade by trade with real lot sizes, commission, spread and the guard. Monte Carlo of pass/fail odds. |
| **TradingView** (`pine/`) | Same strategies in Pine Script v5 for charts and TradingView's Strategy Tester. |
| **FundedNext MCP** (via Claude) | Account health, rules, news calendar, trade review. See [FundedNext MCP](#fundednext-mcp-via-claude). |

## Your account's rules (read from the FundedNext MCP)

| Rule | Value | How the bot handles it |
|---|---|---|
| Plan | Stellar Instant 2K, MT5, `FundedNext-Server` | `prop.initial_balance: 2000` |
| EAs / bots | Allowed below $50,000 | Live mode refuses to start at $50k+ |
| Daily loss limit | None | Optional personal stop: `personal_daily_stop_pct: 2` ($40/day) |
| Max loss | **6% trailing ($120)**, trails the highest balance, capped at $2,000 | Guard never risks more than 80% of the room (`safety_buffer`) |
| Risk limit | 3% per trade idea | Trades risking more than $60 are refused |
| Commission | $7 per lot, on open | Counted in every trade's risk and in the backtest |
| News | Allowed, but only 40% of profit counts within ±5 min of high-impact news | No new trades ±10 min around events in `data/news.json` |
| Quick Strike | Trades closed within 30 s are flagged | 1:3 stops/targets are far from entry, so normal trades don't do this |
| Weekend holding | Allowed | — |

**Why 0.25% risk per trade:** $120 of room at 0.5% ($10) is about 12 straight losses. A 1:3
system that wins 25–30% of the time often has losing streaks of 10+. At 0.25% ($5) it takes
about 19 straight losses to reach the guard. The backtest's Monte Carlo shows the odds at
each risk level, so check them before changing this.

## Strategies

Both enter on a **closed candle** and use a fixed **1:3 risk/reward**. Each strategy is coded
for longs only; shorts come from running the same code on the chart flipped upside down,
so both sides are always exactly symmetrical (verified by tests).

### `breakout_retest`: breakout + retest, Fibonacci, RSI, Stochastic
1. A confirmed swing high breaks: a candle **closes** above it.
2. Price pulls back and **retests** the broken level (within 0.3 × ATR).
3. The pullback depth of the breakout leg is inside the **Fib 38.2–61.8%** zone.
4. Entry candle: **bullish close** back above the level, body ≥ 50% of its range,
   **RSI ≥ 45**, **Stochastic** %K above %D after dipping to ≤ 30, price above the **200 EMA**.
5. Stop below the retest low (−0.2 × ATR). Target 3R.

### `ict_sweep`: liquidity sweep, CISD, FVG (SMT optional)
1. **Liquidity sweep**: price trades below the latest swing low or the previous day's low.
2. **CISD**: a candle closes above the open of the bearish run that made the sweep.
3. **SMT** (optional, `smt: true`): the correlated pair (EURUSD ↔ GBPUSD) did *not* make its own new low.
4. **FVG**: a bullish fair value gap formed after the sweep. Entry when price trades back
   into it and closes bullish.
5. Kill zones only (London 07–10, New York 12–15 UTC). Stop below the sweep low; target 3R.

The backtest runs `ict_sweep` both with and without SMT so you can see whether SMT helps.

## Setup (Windows PC or Windows VPS with MT5)

The `MetaTrader5` Python package only works on **Windows**, next to a running MT5 terminal.

```bash
git clone <this repo> && cd <repo>
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy config\config.example.yaml config\config.yaml
```

1. In MT5: log in to your FundedNext account and enable **Tools → Options → Expert Advisors → Allow algorithmic trading**.
2. Set credentials as environment variables rather than in the file:
   `set MT5_LOGIN=<your login>`, `set MT5_PASSWORD=...`, `set MT5_SERVER=FundedNext-Server`.
3. In `config/config.yaml`:
   - `server_utc_offset_hours`: MT5 server time minus UTC (compare the Market Watch clock with UTC; often 2 or 3).
   - `prop.high_water_balance`: your highest balance so far, so the trailing floor matches FundedNext's.
     It equals the dashboard's `trailing_drawdown` + 120 (currently 1905.79 + 120 = **2025.79**).
   - Copy `data/news.example.json` to `data/news.json` and fill in this week's events.
4. **Paper mode first** (`mode: paper`): signals and position sizes are logged, no orders are sent.
   ```bash
   python -m bot.run --config config/config.yaml
   ```
5. Go live only after the backtest and a few weeks of paper trading look right: `mode: live`.

The bot only manages its own positions (magic number `150820`). Your manual trades are
left alone, but their risk **does** count against the shared $120 room. A manual trade
**without a stop loss** makes the guard block every new bot trade until it's closed.

## Backtesting

Get data: either

```bash
# Free Dukascopy history (UTC), any machine with internet:
python -m bot.download_dukascopy --symbols EURUSD GBPUSD --start 2022-01-01 --end 2025-12-31 --timeframe M15
# or from your MT5 terminal (server time):
python -m bot.export_data --symbol EURUSD --timeframe M15 --bars 100000
```

or export a chart from TradingView (paid plans: chart menu → *Export chart data*) and save
it as `data/EURUSD_M15.csv`.

Run:

```bash
python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv
# Out-of-sample check: decide on parameters using older data only, then test on newer data
python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv --end 2024-12-31
python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv --start 2025-01-01
```

The report covers, per strategy: trades, win rate, average R, profit factor, net $, max
drawdown, longest losing streak, commission, closest distance to the max-loss floor,
whether the account would have been **breached**, days the guard stopped trading, and a
**Monte Carlo** of the odds of reaching +8% (Instant scale-up target) before the max loss,
at 0.25%, 0.5% and 1% risk.

How to read it: you need **hundreds of trades**, a **profit factor clearly above 1.2 after
costs**, and an edge that also holds **out of sample**. A 1:3 system breaks even at roughly a
27% win rate once costs are included. If parameters only work on the data they were tuned
on, the edge isn't real.

## TradingView

`pine/breakout_retest.pine` and `pine/ict_sweep.pine` reproduce the Python logic, the risk
sizing and the trailing-max-loss guard. To use one: Pine Editor → paste → *Add to chart*, then
open the **Strategy Tester** tab. Use a 15-minute EURUSD/GBPUSD chart. A panel shows
balance, max-loss floor and room left. Small differences from the Python results are
expected (data feed, RSI warm-up, day boundaries).

## FundedNext MCP (via Claude)

FundedNext's MCP is read-only: it can't place trades or provide price history. Use it from
Claude (Claude Code or claude.ai with the FundedNext connector) for:

- **Account health before each session**: "Check my FundedNext Stellar Instant account: balance,
  trailing max-loss floor and room left." (`get_account_overview`)
- **Rules check**: "What rules apply to my account?" (`get_account_applicable_rules`)
- **News file**: "Get this week's high-impact USD, EUR and GBP news from the FundedNext
  calendar and write it to data/news.json in the bot's format." (`get_week_wise_news_calendar`)
- **Trade review**: "Review my last 50 trades: win rate, average R, and any risk-limit or
  Quick Strike flags." (`get_trading_history`, `get_risk_parameters`)

## Tests

```bash
python -m pytest -q
```

They cover indicators, the trailing max-loss math (checked against your dashboard's
1905.79 floor), the guard, sizing, the news filter, the Dukascopy decoder, both
strategies' **no-look-ahead** guarantee (a signal never changes when future candles are
added) and long/short symmetry.

## Layout

```
bot/
  run.py               live / paper loop
  backtest.py          account-level backtest + Monte Carlo
  risk.py              sizing + FundedNext guard
  news.py              news blackout
  strategies/          base.py, breakout_retest.py, ict_sweep.py
  broker/              MT5 adapter
  download_dukascopy.py, export_data.py, data.py, config.py
config/config.example.yaml
pine/                  TradingView versions
tests/
```

Trading carries real risk of loss. This is a tool, not financial advice.
