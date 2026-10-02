"""ICT-style liquidity sweep -> SMT divergence -> CISD -> FVG entry.

Long setup, bar by bar (shorts are the mirror image):

1. Liquidity pools: the latest confirmed swing low (pivot_left / pivot_right) and,
   if use_prev_day, the previous server day's low.
2. Sweep: a bar trades below a pool (sell-side liquidity taken). The sweep low keeps
   updating while price keeps pushing lower.
3. CISD (change in state of delivery): a candle closes above the open of the
   bearish run that delivered price into the sweep low, within sweep_max_bars of
   the sweep low.
4. SMT divergence (optional, smt=True): over the same window, the correlated pair did NOT
   take its own equivalent low. E.g. EURUSD sweeps its low but GBPUSD holds.
5. FVG: a bullish fair value gap formed after the sweep low (bar j's low above bar
   j-2's high, at least min_fvg_atr * ATR tall).
6. Entry on the close of a candle that trades back into the FVG and closes above
   its bottom (bullish close if require_bullish_close), within fvg_wait_bars of the
   CISD. Stop below the sweep low minus sl_buffer_atr * ATR; take profit at rr x risk.

The setup resets if a candle closes below the sweep low, or a step times out.
`sessions` (UTC) restricts entries to kill zones, e.g. London and New York opens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import indicators as ta
from .base import StrategyBase

IDLE, SWEPT, AWAIT_ENTRY = 0, 1, 2


@dataclass
class IctSweep(StrategyBase):
    name: str = "ict_sweep"
    sessions: list[str] = field(default_factory=lambda: ["07:00-10:00", "12:00-15:00"])
    pivot_left: int = 3
    pivot_right: int = 3
    use_prev_day: bool = True
    smt: bool = False
    sweep_max_bars: int = 10
    fvg_wait_bars: int = 15
    min_fvg_atr: float = 0.1
    require_bullish_close: bool = True
    atr_period: int = 14
    sl_buffer_atr: float = 0.1
    cisd_lookback: int = 20

    def __post_init__(self):
        self.uses_correlated = self.smt

    def find_signals(self, df, correlated=None):
        if self.smt and correlated is None:
            raise ValueError("ict_sweep with smt=True needs correlated data (or set smt: false)")
        return super().find_signals(df, correlated if self.smt else None)

    def _long_signals(self, df: pd.DataFrame, correlated: pd.DataFrame | None):
        o = df["open"].to_numpy()
        h = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        c = df["close"].to_numpy()
        atr = ta.atr(df["high"], df["low"], df["close"], self.atr_period).to_numpy()
        swing_lows = ta.pivot_highs(-df["low"], self.pivot_left, self.pivot_right)
        day = df["time"].dt.normalize()
        pdl = _previous_day_low(df["low"], day).to_numpy()
        day_codes = day.to_numpy()
        corr_low = correlated["low"].to_numpy() if correlated is not None else None
        corr_pdl = _previous_day_low(correlated["low"], day).to_numpy() if correlated is not None else None

        swing = None              # (level, bar) of the latest unswept swing low
        pdl_taken_day = None      # server day whose previous-day low was already swept
        state = IDLE
        pool_ref = None           # ("swing", bar) or ("pdl", bar) for the SMT reference
        sweep_start = sweep_low_bar = cisd_bar = 0
        sweep_low = cisd_level = 0.0
        fvg = None                # (top, bottom, formed_bar)

        for i in range(2, len(df)):
            if swing_lows[i]:
                bar = i - self.pivot_right
                swing = (lo[bar], bar)
            if np.isnan(atr[i]):
                continue

            if state == SWEPT:
                if lo[i] < sweep_low:
                    sweep_low, sweep_low_bar = lo[i], i
                    cisd_level = self._cisd_level(o, c, sweep_low_bar)
                if i - sweep_low_bar > self.sweep_max_bars:
                    state = IDLE
                elif cisd_level is not None and c[i] > cisd_level:
                    if self.smt and not self._smt_ok(corr_low, corr_pdl, pool_ref, sweep_start, i):
                        state = IDLE
                    else:
                        state, cisd_bar, fvg = AWAIT_ENTRY, i, None
                        for j in range(sweep_low_bar + 2, i + 1):
                            fvg = self._fvg_at(j, h, lo, atr) or fvg

            elif state == AWAIT_ENTRY:
                if c[i] < sweep_low or i - cisd_bar > self.fvg_wait_bars:
                    state = IDLE
                else:
                    if fvg is not None and fvg[2] < i and lo[i] <= fvg[0] and c[i] > fvg[1] \
                            and (c[i] > o[i] or not self.require_bullish_close):
                        sl = sweep_low - self.sl_buffer_atr * atr[i]
                        yield i, c[i], sl, {"sweep": sweep_low, "fvg_near": fvg[0], "fvg_far": fvg[1]}
                        state = IDLE
                        continue
                    if fvg is not None and c[i] < fvg[1]:
                        fvg = None
                    fvg = self._fvg_at(i, h, lo, atr) or fvg

            if state == IDLE:
                pools = []
                if swing is not None and lo[i] < swing[0]:
                    pools.append((swing[0], ("swing", swing[1])))
                if self.use_prev_day and not np.isnan(pdl[i]) and lo[i] < pdl[i] and pdl_taken_day != day_codes[i]:
                    pools.append((pdl[i], ("pdl", i)))
                if pools:
                    _, pool_ref = min(pools, key=lambda p: p[0])
                    if any(ref[0] == "swing" for _, ref in pools):
                        swing = None
                    if any(ref[0] == "pdl" for _, ref in pools):
                        pdl_taken_day = day_codes[i]
                    state = SWEPT
                    sweep_start = sweep_low_bar = i
                    sweep_low = lo[i]
                    cisd_level = self._cisd_level(o, c, i)

    def _cisd_level(self, o, c, end: int):
        """Open of the bearish run that delivered price into bar `end`."""
        m = end
        floor = max(0, end - self.cisd_lookback)
        while m >= floor and not c[m] < o[m]:
            m -= 1
        if m < floor:
            return None
        while m - 1 >= floor and c[m - 1] < o[m - 1]:
            m -= 1
        return o[m]

    def _fvg_at(self, j, h, lo, atr):
        if j < 2 or lo[j] <= h[j - 2]:
            return None
        if lo[j] - h[j - 2] < self.min_fvg_atr * atr[j]:
            return None
        return lo[j], h[j - 2], j

    def _smt_ok(self, corr_low, corr_pdl, pool_ref, start, end) -> bool:
        """Bullish SMT: the correlated pair did not take its equivalent low."""
        kind, bar = pool_ref
        if kind == "swing":
            ref_window = corr_low[max(0, bar - self.pivot_left):bar + self.pivot_right + 1]
            ref = np.nanmin(ref_window) if not np.all(np.isnan(ref_window)) else np.nan
        else:
            ref = corr_pdl[bar]
        window = corr_low[start:end + 1]
        if np.isnan(ref) or np.all(np.isnan(window)):
            return False
        return np.nanmin(window) > ref


def _previous_day_low(low: pd.Series, day: pd.Series) -> pd.Series:
    daily = low.groupby(day.values).min()
    prev = daily.shift(1)
    return pd.Series(day.map(prev).to_numpy(), index=low.index, dtype=float)
