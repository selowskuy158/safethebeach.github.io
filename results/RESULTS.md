# Backtest results: EURUSD + GBPUSD M15, 2023–2025

Run locally on 2026-10-02 from branch `claude/cloud-session-eqlob7`, following `HANDOFF.md`.
`python -m pytest -q`: 40 passed. Defaults from `config/config.example.yaml`: $2,000, 0.25% risk,
6% trailing max loss, $7/lot commission, 0.2 pip slippage, spreads 0.4 (EURUSD) / 0.7 (GBPUSD).

**Correction to the first push (`bcea766`):** the 2025 numbers there were built with HistData
timestamps shifted a fixed +5h, which is one hour off during European summer time (see Data).
Every number below uses the corrected data. `breakout_retest` and `ict_sweep` came out the same
for 2025; `ict_sweep+smt` changed (71 → 46 trades, PF 0.80 → 0.55).

## Verdict

None of the three strategies meets the README bar (profit factor clearly above 1.2 after costs,
holding out of sample). The best in-sample result, `ict_sweep+smt` at PF 1.08, falls to 0.55 in
2025. `hour_profile.py`: 0 of 24 pre-registered fix windows pass, so the data shows no tradable
fix pattern after costs.

## Data

Dukascopy throttled the home IP too: 429 on `bot.download_dukascopy`, then 1–4 day-files a
minute through `dukascopy_prefetch.py` (around 11 hours for the full period). Instead:

- **HistData.com M1 bid**, 2023–2025, converted by `histdata_to_csv.py` with the same
  left-closed M15 resampling as `bot.download_dukascopy`.
- **Timezone:** HistData documents its timestamps as fixed EST, but checked against Dukascopy they
  are UTC−5 in winter and UTC−4 while *Europe* is on summer time (switching 26 Mar 2023, not the US
  date of 12 Mar). The converter reads HistData + 5h as Europe/London time.
- **Same feed check:** on every cached Dukascopy day (173 EURUSD and 141 GBPUSD days from Jan–Jul
  2023, plus 7 EURUSD days around the October 2025 change), HistData and Dukascopy M1 closes at the
  same UTC minute agree on 99.997% of 284,791 pairs (within 0.05 pip). That includes 12–26 March 2023
  and 27–29 October 2025, the windows where the EU and US DST rules disagree.
- **HistData gap:** from 2023-02-20 to 2023-07-28 HistData misses about a quarter of the hours
  (~68 of 96 bars a day). Those days were fetched from Dukascopy (`dukascopy_prefetch.py`,
  2023-02-17 → 2023-07-31) and replace HistData's. After patching, only holidays (25 Dec, 1 Jan)
  are thin, and each year has about 24,900 M15 bars per pair.

Rebuild the CSVs:

    python histdata_to_csv.py --src data/histdata --duka-cache data/duka_cache

## In-sample: 2023-01-01 → 2024-12-31

`python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv --end "2024-12-31 23:59" --trades-out data/is`
and `python breach_count.py --start 2023-01-01 --end "2024-12-31 23:59"`.

| | breakout_retest | ict_sweep | ict_sweep+smt |
|---|---|---|---|
| Trades | 32 ⚠️ statistically weak | 346 | 143 |
| Win rate | 18.8% | 26.3% | 27.3% |
| Profit factor | 0.64 | 1.02 | 1.08 |
| Net $ | −40.89 | +24.90 | +32.24 |
| Max drawdown | $94.94 / 4.73% | $121.72 / 5.67% | $54.14 / 2.63% |
| Longest losing streak | 16 | 16 | 9 |
| Closest to max-loss floor | $31.19 | $24.90 | $70.54 |
| 6% trailing breaches | 0 | 0 | 0 |
| MC P(+8% first) at 0.25 / 0.5 / 1% | 0% / 1% / 5% | 22% / 22% / 19% | 32% / 25% / 21% |
| MC P(max loss first) at 0.25 / 0.5 / 1% | 100% / 99% / 95% | 78% / 78% / 81% | 68% / 75% / 79% |

`ict_sweep` had the guard block 114 entries near the floor and skipped 238 while already in a trade.

## Out-of-sample: 2025-01-01 → 2025-12-31

`python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv --start 2025-01-01 --trades-out data/oos`
and `python breach_count.py --start 2025-01-01 --end "2025-12-31 23:59"`.

| | breakout_retest | ict_sweep | ict_sweep+smt |
|---|---|---|---|
| Trades | 18 ⚠️ statistically weak | 42 ⚠️ statistically weak | 46 ⚠️ statistically weak |
| Win rate | 27.8% | 11.9% | 15.2% |
| Profit factor | 1.00 | 0.37 | 0.55 |
| Net $ | −0.01 | −93.04 | −74.19 |
| Max drawdown | $24.74 / 1.22% | $101.47 / 5.05% | $96.55 / 4.77% |
| Longest losing streak | 4 | 17 | 11 |
| Closest to max-loss floor | $95.54 | $24.14 | $25.85 |
| 6% trailing breaches | 0 | 0 | 0 |
| MC P(+8% first) at 0.25 / 0.5 / 1% | too few trades | 0% / 0% / 1% | 0% / 0% / 3% |
| MC P(max loss first) at 0.25 / 0.5 / 1% | too few trades | 100% / 100% / 99% | 100% / 100% / 97% |

The low 2025 trade counts for the ICT variants are partly the guard: once equity sat about $24
above the floor, it blocked 263 (`ict_sweep`) and 23 (`+smt`) entries. Without that, both would
probably have breached.

## hour_profile.py

`python hour_profile.py` on the data above; printout in `hour_profile.txt`, tables in
`hour_profile_*.csv`, `fix_windows_*.csv`, `fix_window_verdict.csv`.

- **Fix windows: 0 of 24 pass.** Signs flip between periods (e.g. EURUSD ECB 2h after: −0.79 pips
  in-sample, +4.24 in 2025; London 4h before: −2.09 then +3.97).
- **Tokyo, against the prediction:** EURUSD rises in the 4h before the Tokyo fix in both periods
  (+1.55 pips, t 3.34; +3.05, t 2.62). That is the opposite of the predicted USD bid. It's one of
  24 tests and close to the 1.5 pip cost, so treat it as a lead at most.
- **Hour of day:** the only hour that is strong in both pairs and both periods is 23:00 London
  (+1.2 to +3.2 pips, t 3.5–6.4), right after a weak 21:00. That is the daily rollover, when spreads
  widen. On bid-only data that shows as a bid dip and recovery, which is a quoting artifact and not
  tradable.

## TradingView cross-check

Not run. The TradingView account is on the Basic plan: 2 indicators per chart (both slots in use)
and about 5,000 M15 bars of history (~2–3 months, July–Oct 2026), so it cannot cover 2023–2025.

## Files

- `is_report.txt`, `oos_report.txt`: full `bot.backtest` output; `is_breaches.txt`, `oos_breaches.txt`: `breach_count.py`.
- `is_*.csv`, `oos_*.csv`: trade lists per strategy.
- `hour_profile.txt` and the `hour_profile_*` / `fix_window*` CSVs: `hour_profile.py`.
