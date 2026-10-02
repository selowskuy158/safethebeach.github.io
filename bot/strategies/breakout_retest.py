"""Breakout + retest with Fibonacci, RSI, Stochastic and candle-close confirmation.

Long setup, bar by bar (shorts are the mirror image):

1. Swing high: a confirmed pivot high (pivot_left / pivot_right bars).
2. Breakout: a candle closes above that swing high (previous close was at or below).
   Impulse leg A -> B: A = lowest low from the swing high to the breakout bar,
   B = highest high after the breakout until price first comes back.
3. Retest: a later bar's low trades back to the level (within retest_tolerance_atr).
4. Entry on the close of a candle that, while the retest is live:
   - is bullish with a body of at least min_body_ratio of its range,
   - closes back above the level,
   - has a retest depth (B - retest low) / (B - A) inside [fib_min, fib_max],
   - has RSI >= rsi_long_min (momentum still on the buyers' side),
   - has Stoch %K above %D, with %K at or below stoch_oversold within the last
     stoch_lookback bars (the pullback has turned back up),
   - is above the trend EMA (ema_trend = 0 disables the filter).
5. Stop loss below the retest low minus sl_buffer_atr * ATR; take profit at rr x risk.

The setup is cancelled if a candle closes back below the level - tolerance (failed
breakout) or no entry comes within max_retest_bars.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import indicators as ta
from .base import StrategyBase


@dataclass
class BreakoutRetest(StrategyBase):
    name: str = "breakout_retest"
    pivot_left: int = 5
    pivot_right: int = 5
    ema_trend: int = 200
    rsi_period: int = 14
    rsi_long_min: float = 45.0
    stoch_k: int = 14
    stoch_smooth: int = 3
    stoch_d: int = 3
    stoch_oversold: float = 30.0
    stoch_lookback: int = 5
    fib_min: float = 0.382
    fib_max: float = 0.618
    atr_period: int = 14
    retest_tolerance_atr: float = 0.3
    sl_buffer_atr: float = 0.2
    max_retest_bars: int = 20
    min_body_ratio: float = 0.5

    def _long_signals(self, df: pd.DataFrame, correlated: pd.DataFrame | None):
        o = df["open"].to_numpy()
        h = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        c = df["close"].to_numpy()
        rsi = ta.rsi(df["close"], self.rsi_period).to_numpy()
        k, d = ta.stochastic(df["high"], df["low"], df["close"], self.stoch_k, self.stoch_smooth, self.stoch_d)
        k, d = k.to_numpy(), d.to_numpy()
        k_low = pd.Series(k).rolling(self.stoch_lookback, min_periods=1).min().to_numpy()
        atr = ta.atr(df["high"], df["low"], df["close"], self.atr_period).to_numpy()
        ema = ta.ema(df["close"], self.ema_trend).to_numpy() if self.ema_trend else None
        pivots = ta.pivot_highs(df["high"], self.pivot_left, self.pivot_right)

        swing_level = swing_bar = None
        active = False
        level = a = b = retest_low = 0.0
        start = 0
        touched = False

        for i in range(1, len(df)):
            if pivots[i]:
                swing_bar = i - self.pivot_right
                swing_level = h[swing_bar]

            if np.isnan(atr[i]):
                continue
            tol = self.retest_tolerance_atr * atr[i]

            if active:
                if i - start > self.max_retest_bars or c[i] < level - tol:
                    active = False
                else:
                    if not touched:
                        if lo[i] <= level + tol:
                            touched = True
                            retest_low = lo[i]
                        else:
                            b = max(b, h[i])
                    else:
                        retest_low = min(retest_low, lo[i])

                    if touched and self._confirmed(i, o, h, lo, c, rsi, k, d, k_low, ema, level, a, b, retest_low):
                        sl = retest_low - self.sl_buffer_atr * atr[i]
                        yield i, c[i], sl, {"level": level, "retest": retest_low}
                        active = False

            if not active and swing_level is not None and c[i] > swing_level >= c[i - 1]:
                active = True
                level = swing_level
                a = lo[swing_bar:i + 1].min()
                b = h[i]
                start = i
                touched = False
                swing_level = None

    def _confirmed(self, i, o, h, lo, c, rsi, k, d, k_low, ema, level, a, b, retest_low) -> bool:
        rng = h[i] - lo[i]
        if rng <= 0 or c[i] <= o[i] or (c[i] - o[i]) / rng < self.min_body_ratio:
            return False
        if c[i] <= level or b <= a:
            return False
        depth = (b - retest_low) / (b - a)
        if not self.fib_min <= depth <= self.fib_max:
            return False
        if np.isnan(rsi[i]) or rsi[i] < self.rsi_long_min:
            return False
        if np.isnan(k[i]) or np.isnan(d[i]) or k[i] <= d[i] or k_low[i] > self.stoch_oversold:
            return False
        if ema is not None and c[i] <= ema[i]:
            return False
        return True
