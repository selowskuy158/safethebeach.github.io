"""Build data/<SYMBOL>_M15.csv from HistData.com M1 ASCII files instead of Dukascopy.

HistData timestamps are EST without daylight saving (fixed UTC-5), so they are
shifted +5h to UTC. Resampling matches bot.download_dukascopy (left-labelled,
left-closed bars). HistData's 2023 files miss about a quarter of the hours from
20 Feb to 28 Jul 2023, so any day cached by dukascopy_prefetch.py (--duka-cache)
replaces HistData's bars for that UTC day. Run from the repo root:
    python histdata_to_csv.py --src data/histdata --duka-cache data/duka_cache
"""
import argparse
import lzma
from pathlib import Path

import pandas as pd

from bot.download_dukascopy import RECORD, RESAMPLE, price_scale


def duka_day(path: Path, symbol: str) -> pd.DataFrame:
    """Parse one cached Dukascopy day file exactly like bot.download_dukascopy.fetch_day."""
    data = lzma.decompress(path.read_bytes()) if path.stat().st_size else b""
    rows = [RECORD.unpack_from(data, off) for off in range(0, len(data), RECORD.size)]
    df = pd.DataFrame(rows, columns=["sec", "open", "close", "low", "high", "volume"])
    df = df[df["volume"] > 0]
    df["time"] = pd.Timestamp(path.stem) + pd.to_timedelta(df["sec"], unit="s")
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] / price_scale(symbol)
    return df.set_index("time")[["open", "high", "low", "close"]]


ap = argparse.ArgumentParser()
ap.add_argument("--src", default="data/histdata")
ap.add_argument("--duka-cache", help="dukascopy_prefetch.py cache; its days override HistData")
ap.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
ap.add_argument("--years", nargs="+", default=["2023", "2024", "2025"])
ap.add_argument("--timeframe", default="M15", choices=list(RESAMPLE))
ap.add_argument("--out-dir", default="data")
args = ap.parse_args()

for sym in args.symbols:
    parts = [pd.read_csv(Path(args.src) / f"DAT_ASCII_{sym}_M1_{y}.csv", sep=";", header=None,
                         names=["time", "open", "high", "low", "close", "volume"]) for y in args.years]
    m1 = pd.concat(parts)
    m1["time"] = pd.to_datetime(m1["time"], format="%Y%m%d %H%M%S") + pd.Timedelta(hours=5)  # EST -> UTC
    m1 = m1.drop(columns="volume").drop_duplicates("time").set_index("time").sort_index()
    cached = sorted(Path(args.duka_cache, sym).glob("*.bi5")) if args.duka_cache else []
    if cached:
        days = {pd.Timestamp(p.stem) for p in cached}
        duka = pd.concat([duka_day(p, sym) for p in cached])
        m1 = pd.concat([m1[~m1.index.normalize().isin(days)], duka]).sort_index()
        print(f"{sym}: {len(days)} days taken from the Dukascopy cache")
    bars = m1.resample(RESAMPLE[args.timeframe], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    out = Path(args.out_dir) / f"{sym}_{args.timeframe}.csv"
    bars.to_csv(out, index=False)
    print(f"{sym}: {len(bars)} {args.timeframe} bars {bars['time'].iat[0]} -> {bars['time'].iat[-1]} -> {out}")
