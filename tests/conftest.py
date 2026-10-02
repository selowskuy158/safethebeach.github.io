import numpy as np
import pandas as pd
import pytest


def random_walk(seed: int, n: int = 6000, base: float = 1.10, common=None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0, 0.0004, n) if common is None else common + rng.normal(0, 0.0002, n)
    close = base * np.exp(np.cumsum(shocks))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n)) * base
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n)) * base
    time = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame({"time": time, "open": open_, "high": high, "low": low, "close": close})


@pytest.fixture
def pair():
    """Two correlated 15-minute series (EURUSD-like, GBPUSD-like)."""
    common = np.random.default_rng(99).normal(0, 0.0004, 6000)
    return random_walk(1, common=common), random_walk(2, base=1.27, common=common)
