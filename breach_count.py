"""Count every breach of the 6% trailing max loss in a period.

bot.backtest stops the account at the first breach, so it can only report
"breached: yes/no". This restarts a fresh $2,000 account on the bar after each
breach and keeps going, using the repo's own simulate_account and config.

Run from the repo root:
    python breach_count.py --start 2023-01-01 --end "2024-12-31 23:59"   # in-sample
    python breach_count.py --start 2025-01-01 --end "2025-12-31 23:59"   # out-of-sample
"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

from bot.backtest import DEFAULT_CONFIG, simulate_account
from bot.config import strategy_from_config
from bot.data import load_csv

ap = argparse.ArgumentParser()
ap.add_argument("--start", required=True)
ap.add_argument("--end", required=True)
ap.add_argument("--data-dir", default="data")
ap.add_argument("--config", default=str(DEFAULT_CONFIG))
args = ap.parse_args()

start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
cfg = yaml.safe_load(Path(args.config).read_text())
full = {}
for sym in ("EURUSD", "GBPUSD"):
    df = load_csv(f"{args.data_dir}/{sym}_M15.csv")
    full[sym] = df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)

variants = [("breakout_retest", "breakout_retest", {}), ("ict_sweep", "ict_sweep", {}),
            ("ict_sweep+smt", "ict_sweep", {"smt": True})]
for label, name, overrides in variants:
    strategy = strategy_from_config(cfg, name, **overrides)
    t0, breaches = start, []
    while True:
        frames = {s: d[d["time"] >= t0].reset_index(drop=True) for s, d in full.items()}
        if min(len(d) for d in frames.values()) < 1000:  # too little data left to restart
            break
        res = simulate_account(frames, strategy, cfg, traded=["EURUSD", "GBPUSD"])
        if res.breached_at is None:
            break
        breaches.append(f"{res.breached_at:%Y-%m-%d} (after {len(res.trades)} trades)")
        t0 = res.breached_at + pd.Timedelta(minutes=15)
    print(f"{label}: {len(breaches)} breach(es) {breaches}", flush=True)
