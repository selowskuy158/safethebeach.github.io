"""Technical indicators. Formulas follow TradingView's built-ins (ta.rsi, ta.stoch,
ta.atr, ta.ema) so the Python bot and the Pine scripts agree. RSI/ATR use Wilder's
smoothing via an EWM, which differs from TradingView only in the first few bars."""

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    avg_gain = rma(delta.clip(lower=0.0), length)
    avg_loss = rma(-delta.clip(upper=0.0), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_length: int = 14, k_smooth: int = 3, d_length: int = 3):
    """Returns (%K, %D) like `ta.sma(ta.stoch(close, high, low, k), smooth)`."""
    lowest = low.rolling(k_length).min()
    highest = high.rolling(k_length).max()
    rng = (highest - lowest).replace(0.0, np.nan)
    raw = 100.0 * (close - lowest) / rng
    k = raw.rolling(k_smooth).mean()
    d = k.rolling(d_length).mean()
    return k, d


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return rma(tr, length)


def pivot_highs(high: pd.Series, left: int, right: int) -> np.ndarray:
    """Boolean array marking the bar where a swing high is *confirmed*.

    flags[i] is True when bar i - right is the highest high of bars
    [i - right - left, i]. The swing is only known `right` bars later, so using
    this array never looks ahead.
    """
    window_max = high.rolling(left + right + 1).max().to_numpy()
    candidate = high.shift(right).to_numpy()
    return np.nan_to_num(candidate == window_max, nan=False).astype(bool)
