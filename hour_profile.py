"""Step 1 of the fix-flow research: is there a tradable intraday USD pattern in
your EURUSD/GBPUSD M15 data, after costs, both in-sample and out-of-sample?

Measures, without any strategy code or tuning:
  A. Average move for each London-time hour of the day (DST handled), with
     t-stats and the running intraday path. Breedon & Ranaldo (2013): EURUSD
     tends to fall in the European morning and rise in US hours.
  B. Moves before and after the three benchmark fixes, using windows fixed in
     advance. Krohn, Mueller & Whelan (2024): the USD rises into each fix and
     falls after it, so the pair (EURUSD, GBPUSD) should FALL before the fix
     and RISE after it.
       Tokyo  09:55 Asia/Tokyo     (M15 data: measured at 10:00, the first bar edge after it)
       ECB    14:15 Europe/Berlin
       London 16:00 Europe/London  (WM/Reuters)
     Windows: 4h and 2h before, 2h and 4h after. 3 fixes x 4 windows x 2 pairs = 24 tests.

A window counts as PASS only if, in EVERY period, the move has the predicted sign,
t >= 2, and the average move is bigger than the round-trip cost (spread +
2 x slippage + commission, all from the config). With that two-period rule, the
chance that one of the 24 tests passes by luck is well under 1%.

Run from the repo root (data in UTC, e.g. from bot.download_dukascopy or histdata_to_csv.py):
    python hour_profile.py
    python hour_profile.py --periods IS:2023-01-01:"2024-12-31 23:59" OOS:2025-01-01:"2025-12-31 23:59"
Writes CSVs to --out (default results/).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from bot.backtest import DEFAULT_CONFIG
from bot.data import load_csv, pip_size

FIXES = {  # name: (local time, timezone)
    "Tokyo": ("10:00", "Asia/Tokyo"),
    "ECB": ("14:15", "Europe/Berlin"),
    "London": ("16:00", "Europe/London"),
}
PRE, POST = [4, 2], [2, 4]  # hours
PATH_OFFSETS = [-6, -4, -2, -1, 0, 1, 2, 4, 6]  # hours, for the printed event path


def round_trip_cost_pips(cfg: dict, sym: str) -> float:
    bt, risk = cfg.get("backtest", {}), cfg["risk"]
    spreads = bt.get("spread_pips", {}) or {}
    spread = float(spreads.get(sym, spreads.get("default", 1.0)))
    slip = float(bt.get("slippage_pips", 0.0))
    pip_value_per_lot = float(bt.get("contract_size", 100_000)) * pip_size(sym)  # USD per pip, XXXUSD
    commission = float(risk.get("commission_per_lot", 0.0)) / pip_value_per_lot
    return spread + 2 * slip + commission


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")


def price_at(df: pd.DataFrame) -> pd.Series:
    """Price at each bar edge t = close of the M15 bar that ends at t."""
    s = df.set_index("time")["close"]
    s.index = s.index + pd.Timedelta(minutes=15)
    return s


def px(prices: pd.Series, when: pd.DatetimeIndex) -> np.ndarray:
    return prices.reindex(when).to_numpy()


def hour_profile(prices: pd.Series, pip: float, start, end) -> pd.DataFrame:
    grid = pd.date_range(pd.Timestamp(start).ceil("h"), pd.Timestamp(end).floor("h"), freq="h")
    p = prices.reindex(grid)
    r = (p.shift(-1) - p) / pip  # move during the hour starting at t (UTC)
    london_hour = grid.tz_localize("UTC").tz_convert("Europe/London").hour
    df = pd.DataFrame({"r": r.to_numpy(), "h": london_hour}).dropna()
    g = df.groupby("h")["r"]
    out = pd.DataFrame({"mean_pips": g.mean(), "t": g.apply(tstat), "up_%": g.apply(lambda x: (x > 0).mean() * 100),
                        "days": g.size()})
    out["cum_pips"] = out["mean_pips"].cumsum()
    out.index.name = "london_hour"
    return out.round(2)


def fix_events(prices: pd.Series, pip: float, start, end) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    days = days[days.weekday < 5]
    windows, paths = [], []
    for name, (hhmm, tz) in FIXES.items():
        local = pd.DatetimeIndex(days.strftime("%Y-%m-%d") + " " + hhmm)
        t0 = local.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC").tz_localize(None)
        t0 = t0[(t0 >= pd.Timestamp(start)) & (t0 <= pd.Timestamp(end))]
        p0 = px(prices, t0)
        for side, h in [("before", h) for h in PRE] + [("after", h) for h in POST]:
            if side == "before":
                move = (p0 - px(prices, t0 - pd.Timedelta(hours=h))) / pip
                label, predicted = f"{h}h before", -1
            else:
                move = (px(prices, t0 + pd.Timedelta(hours=h)) - p0) / pip
                label, predicted = f"{h}h after", +1
            m = pd.Series(move)
            windows.append({"fix": name, "window": label, "predicted": "down" if predicted < 0 else "up",
                            "mean_pips": m.mean(), "t": tstat(m), "up_%": (m.dropna() > 0).mean() * 100,
                            "days": int(m.notna().sum()), "_sign": predicted})
        path = {f"{k:+d}h": np.nanmean((px(prices, t0 + pd.Timedelta(hours=k)) - p0) / pip) for k in PATH_OFFSETS}
        paths.append({"fix": name, **path})
    return pd.DataFrame(windows), pd.DataFrame(paths).set_index("fix").round(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--periods", nargs="+",
                    default=["IS:2023-01-01:2024-12-31 23:59", "OOS:2025-01-01:2025-12-31 23:59"],
                    help="name:start:end (UTC)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    periods = [p.split(":", 2) for p in args.periods]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200)
    verdict_rows = []

    for sym in args.symbols:
        df = load_csv(Path(args.data_dir) / f"{sym}_M15.csv")
        prices, pip = price_at(df), pip_size(sym)
        cost = round_trip_cost_pips(cfg, sym)
        print(f"\n################ {sym}  (round-trip cost {cost:.2f} pips; pair up = USD weaker)")
        per_period = {}
        for name, start, end in periods:
            hp = hour_profile(prices, pip, start, end)
            win, path = fix_events(prices, pip, start, end)
            hp.to_csv(Path(args.out) / f"hour_profile_{sym}_{name}.csv")
            win.drop(columns="_sign").round(2).to_csv(Path(args.out) / f"fix_windows_{sym}_{name}.csv", index=False)
            print(f"\n=== {sym} {name} {start} -> {end}")
            print("A. Average move per London-time hour (pips):")
            print(hp.T.to_string())
            print("\nB. Average path around each fix (pips vs price at the fix):")
            print(path.to_string())
            print("\n   Fixed windows (predicted: pair down before the fix, up after):")
            print(win.drop(columns="_sign").round(2).to_string(index=False))
            per_period[name] = win

        for i, row in per_period[periods[0][0]].iterrows():
            checks = []
            for name, _, _ in periods:
                w = per_period[name].loc[i]
                ok = (np.sign(w["mean_pips"]) == w["_sign"] and w["t"] * w["_sign"] >= 2
                      and abs(w["mean_pips"]) > cost)
                checks.append((name, w["mean_pips"], w["t"], ok))
            verdict_rows.append({"symbol": sym, "fix": row["fix"], "window": row["window"],
                                 **{f"{n}_pips": round(m, 2) for n, m, _, _ in checks},
                                 **{f"{n}_t": round(t, 2) for n, _, t, _ in checks},
                                 "cost_pips": round(cost, 2),
                                 "PASS": "PASS" if all(c[3] for c in checks) else "-"})

    verdict = pd.DataFrame(verdict_rows)
    verdict.to_csv(Path(args.out) / "fix_window_verdict.csv", index=False)
    print("\n################ Verdict: predicted sign, t >= 2 and |move| > cost in every period")
    print(verdict.to_string(index=False))
    n_pass = (verdict["PASS"] == "PASS").sum()
    print(f"\n{n_pass} of {len(verdict)} pre-registered windows pass."
          + (" Worth building a time-exit strategy for these." if n_pass else
             " No tradable fix pattern after costs in this data: don't build the strategy."))


if __name__ == "__main__":
    main()
