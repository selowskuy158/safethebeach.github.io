"""Download free historical candles from Dukascopy and save them as CSV.

    python -m bot.download_dukascopy --symbols EURUSD GBPUSD --start 2023-01-01 --end 2025-12-31 --timeframe M15

Dukascopy serves one LZMA-compressed file of 1-minute BID candles per day. They
are resampled to --timeframe and written to data/<SYMBOL>_<TF>.csv in UTC.
Times are UTC: set server_utc_offset_hours: 0 when backtesting this data.
"""

from __future__ import annotations

import argparse
import lzma
import struct
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
RECORD = struct.Struct(">5if")  # seconds from midnight, open, close, low, high, volume
RESAMPLE = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}


def price_scale(symbol: str) -> float:
    s = symbol.upper()
    if "JPY" in s:
        return 1e3
    if s.startswith(("XAU", "XAG")):
        return 1e3
    return 1e5


def fetch_day(symbol: str, day: date, retries: int = 3) -> pd.DataFrame | None:
    url = URL.format(sym=symbol.upper(), y=day.year, m=day.month - 1, d=day.day)  # months are 0-based
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
    if not raw:
        return None
    data = lzma.decompress(raw)
    scale = price_scale(symbol)
    rows = [RECORD.unpack_from(data, off) for off in range(0, len(data), RECORD.size)]
    df = pd.DataFrame(rows, columns=["sec", "open", "close", "low", "high", "volume"])
    df = df[df["volume"] > 0]  # flat filler candles (market closed)
    if df.empty:
        return None
    df["time"] = pd.Timestamp(day) + pd.to_timedelta(df["sec"], unit="s")
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] / scale
    return df[["time", "open", "high", "low", "close"]]


def download(symbol: str, start: date, end: date, timeframe: str, workers: int = 8) -> pd.DataFrame:
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() != 5]  # no trading on Saturdays
    with ThreadPoolExecutor(workers) as pool:
        parts = [p for p in pool.map(lambda d: fetch_day(symbol, d), days) if p is not None]
    if not parts:
        raise RuntimeError(f"No data for {symbol} between {start} and {end}")
    m1 = pd.concat(parts).set_index("time").sort_index()
    if timeframe == "M1":
        return m1.reset_index()
    bars = m1.resample(RESAMPLE[timeframe], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return bars.reset_index()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--timeframe", default="M15", choices=list(RESAMPLE))
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    for symbol in args.symbols:
        df = download(symbol, args.start, args.end, args.timeframe)
        out = Path(args.out_dir) / f"{symbol.upper()}_{args.timeframe}.csv"
        df.to_csv(out, index=False)
        print(f"{symbol}: {len(df)} {args.timeframe} bars {df['time'].iat[0]} -> {df['time'].iat[-1]} -> {out}")


if __name__ == "__main__":
    main()
