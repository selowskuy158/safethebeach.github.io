import numpy as np
import pandas as pd

from bot import indicators as ta


def test_rsi_extremes_and_range():
    up = pd.Series(np.arange(1.0, 60.0))
    assert ta.rsi(up, 14).iloc[-1] == 100.0
    assert ta.rsi(-up, 14).iloc[-1] < 1e-9
    noisy = pd.Series(np.random.default_rng(0).normal(0, 1, 500).cumsum())
    values = ta.rsi(noisy, 14).dropna()
    assert values.between(0, 100).all()


def test_rsi_mirror_identity():
    s = pd.Series(np.random.default_rng(1).normal(0, 1, 300).cumsum() + 100)
    a, b = ta.rsi(s), ta.rsi(-s)
    assert np.allclose((a + b).dropna(), 100.0)


def test_stochastic_bounds():
    rng = np.random.default_rng(2)
    c = pd.Series(rng.normal(0, 1, 400).cumsum() + 50)
    k, d = ta.stochastic(c + 0.5, c - 0.5, c)
    assert k.dropna().between(0, 100).all() and d.dropna().between(0, 100).all()


def test_atr_positive():
    rng = np.random.default_rng(3)
    c = pd.Series(rng.normal(0, 1, 200).cumsum() + 50)
    assert (ta.atr(c + 1, c - 1, c, 14).dropna() > 0).all()


def test_pivot_is_confirmed_late_and_never_looks_ahead():
    high = pd.Series([1, 2, 3, 9, 3, 2, 1, 1, 1], dtype=float)
    flags = ta.pivot_highs(high, left=3, right=3)
    assert list(np.flatnonzero(flags)) == [6]          # bar 3's peak known at bar 6
    # Changing the future (bars after 6) must not change flags up to bar 6.
    altered = high.copy()
    altered.iloc[7:] = 50
    assert list(ta.pivot_highs(altered, 3, 3)[:7]) == list(flags[:7])
