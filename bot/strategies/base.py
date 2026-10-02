"""Shared strategy plumbing.

Every strategy only implements the *long* side in `_long_signals`. Shorts come for
free by running the same code on a mirrored frame (prices negated, highs and lows
swapped): a swing low becomes a swing high, a bearish FVG becomes a bullish one,
RSI(-close) == 100 - RSI(close), and so on. This keeps long and short logic exactly
symmetrical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime

import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass
class Signal:
    index: int            # bar index (in the frame passed in) whose close triggered it
    time: pd.Timestamp
    side: str             # "buy" or "sell"
    entry: float          # reference entry (signal bar close)
    sl: float
    tp: float
    strategy: str
    note: str = ""

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Flip a price frame upside down so short setups look like long setups."""
    out = df.copy()
    out["open"] = -df["open"]
    out["close"] = -df["close"]
    out["high"] = -df["low"]
    out["low"] = -df["high"]
    return out


def parse_sessions(sessions: list[str]) -> list[tuple[dtime, dtime]]:
    """Parse ["07:00-10:00", "12:00-15:00"] into time ranges (UTC)."""
    parsed = []
    for s in sessions or []:
        start, end = s.split("-")
        parsed.append((dtime.fromisoformat(start.strip()), dtime.fromisoformat(end.strip())))
    return parsed


def in_sessions(ts: pd.Timestamp, sessions: list[tuple[dtime, dtime]], utc_offset_hours: float) -> bool:
    """True if `ts` (broker server time) falls in any session (given in UTC)."""
    if not sessions:
        return True
    t = (ts - pd.Timedelta(hours=utc_offset_hours)).time()
    for start, end in sessions:
        if start <= end:
            if start <= t < end:
                return True
        elif t >= start or t < end:  # wraps midnight
            return True
    return False


@dataclass
class StrategyBase:
    name: str = "base"
    rr: float = 3.0
    sessions: list[str] = field(default_factory=list)
    session_utc_offset_hours: float = 0.0
    uses_correlated: bool = False

    def find_signals(self, df: pd.DataFrame, correlated: pd.DataFrame | None = None) -> list[Signal]:
        """Scan a frame of *closed* bars and return all buy and sell signals.

        `df` needs columns time, open, high, low, close. `correlated` (optional) is
        another symbol's frame aligned to df's rows (same length, same times).
        """
        df = df.reset_index(drop=True)
        sessions = parse_sessions(self.sessions)
        signals: list[Signal] = []

        for side, frame, corr in (
            ("buy", df, correlated),
            ("sell", mirror(df), mirror(correlated) if correlated is not None else None),
        ):
            sign = 1.0 if side == "buy" else -1.0
            for idx, entry, sl, prices in self._long_signals(frame, corr):
                if not in_sessions(df["time"].iat[idx], sessions, self.session_utc_offset_hours):
                    continue
                risk = entry - sl
                if risk <= 0:
                    continue
                tp = entry + self.rr * risk
                signals.append(Signal(
                    index=idx, time=df["time"].iat[idx], side=side,
                    entry=sign * entry, sl=sign * sl, tp=sign * tp,
                    strategy=self.name,
                    note=" ".join(f"{k}={sign * v:.5f}" for k, v in prices.items()),
                ))
        signals.sort(key=lambda s: s.index)
        return signals

    def _long_signals(self, df: pd.DataFrame, correlated: pd.DataFrame | None):
        """Yield (bar_index, entry, stop_loss, {label: price}) for long setups.

        The labelled prices end up in Signal.note (flipped back for shorts)."""
        raise NotImplementedError
