"""Load OHLC history from CSV (MT5 export via bot.export_data, or TradingView export)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_ALIASES = {"date": "time", "datetime": "time", "timestamp": "time", "<date>": "time",
            "<open>": "open", "<high>": "high", "<low>": "low", "<close>": "close"}


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in df.columns]
    if "<time>" in df.columns:  # MT5 history-center export splits date and time
        df["time"] = df["time"].astype(str) + " " + df.pop("<time>").astype(str)
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    t = df["time"]
    if pd.api.types.is_numeric_dtype(t):
        df["time"] = pd.to_datetime(t, unit="s")
    else:
        df["time"] = pd.to_datetime(t.astype(str).str.replace(".", "-", regex=False), utc=True).dt.tz_localize(None)
    df = df[["time", "open", "high", "low", "close"]].astype(
        {"open": float, "high": float, "low": float, "close": float})
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def align(primary: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    """Reindex `other` onto `primary`'s bar times (missing bars become NaN)."""
    return (other.set_index("time").reindex(primary["time"]).reset_index())


def pip_size(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith("XAU"):
        return 0.1
    if s.startswith("XAG"):
        return 0.01
    return 0.01 if "JPY" in s else 0.0001
