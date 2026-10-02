# Backtest results: EURUSD + GBPUSD M15, 2023–2025

Run locally on 2026-10-02 from branch `claude/cloud-session-eqlob7`, following `HANDOFF.md`.
`python -m pytest -q`: 40 passed.

**Status:** out-of-sample 2025 done. In-sample 2023–2024 pending (waiting on the Dukascopy
fill for the HistData gap described below); this file will be updated.

## Data

Dukascopy throttled the home IP too: 429 on `bot.download_dukascopy`, then about 3 day-files
a minute through `dukascopy_prefetch.py` (roughly 11 hours for the full period). Instead:

- **HistData.com M1 bid** for 2023, 2024 and 2025, converted with `histdata_to_csv.py`
  (fixed EST to UTC, +5h; same left-closed M15 resampling as `bot.download_dukascopy`).
- **Same feed check:** on the 32 Dukascopy days cached (Jan–Feb 2023), the HistData M15 closes
  match Dukascopy exactly (mean |diff| 0.00 pips; a 1-hour shift gives 5–15 pips). Only winter
  days were checked, so DST-season alignment rests on HistData's documented fixed-EST timestamps.
- **HistData gap:** from 2023-02-20 to 2023-07-28 both pairs miss about a quarter of the hours
  (~68 of 96 bars a day). Those days are being refetched from Dukascopy and patched in with
  `histdata_to_csv.py --duka-cache data/duka_cache`. 2024 and 2025 are complete apart from
  holidays (25 Dec, 1 Jan).

Rebuild the CSVs:

    python histdata_to_csv.py --src data/histdata --duka-cache data/duka_cache

## Out-of-sample: 2025-01-01 → 2025-12-31

`python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv --start 2025-01-01 --trades-out data/oos`
and `python breach_count.py --start 2025-01-01 --end "2025-12-31 23:59"`. Defaults: $2,000,
0.25% risk, 6% trailing max loss, $7/lot, 0.2 pip slippage.

| | breakout_retest | ict_sweep | ict_sweep+smt |
|---|---|---|---|
| Trades | 18 ⚠️ statistically weak | 42 ⚠️ statistically weak | 71 ⚠️ statistically weak |
| Win rate | 27.8% | 11.9% | 22.5% |
| Profit factor | 1.00 | 0.37 | 0.80 |
| Net $ | −0.01 | −93.04 | −45.90 |
| Max drawdown | $24.74 / 1.22% | $101.47 / 5.05% | $87.05 / 4.30% |
| Longest losing streak | 4 | 17 | 12 |
| Closest to max-loss floor | $95.54 | $24.14 | $34.95 |
| 6% trailing breaches | 0 | 0 | 0 |
| MC P(+8% first) at 0.25 / 0.5 / 1% | too few trades | 0% / 0% / 1% | 2% / 7% / 12% |
| MC P(max loss first) at 0.25 / 0.5 / 1% | too few trades | 100% / 100% / 99% | 98% / 93% / 88% |

Notes:
- `ict_sweep` did not breach only because the guard blocked 261 entries once equity was within
  $24 of the floor; its Monte Carlo shows it would almost always hit max loss first.
- Against the README bar (profit factor clearly above 1.2 after costs, holding out of sample):
  **none of the three passes in 2025.**

Raw output: `oos_report.txt`, `oos_breaches.txt`; trade lists: `oos_*.csv`.

## In-sample: 2023-01-01 → 2024-12-31

Pending.

## TradingView cross-check

Not run. The TradingView account is on the Basic plan: 2 indicators per chart (both slots in use)
and about 5,000 M15 bars of history (~2–3 months, July–Oct 2026), so it cannot cover 2023–2025.
A version of each Pine script with `startDate`/`endDate` inputs gating entries was prepared for
when a plan with deep history is available.
