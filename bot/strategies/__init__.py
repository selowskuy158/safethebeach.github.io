from .base import Signal, StrategyBase
from .breakout_retest import BreakoutRetest
from .ict_sweep import IctSweep

STRATEGIES = {
    BreakoutRetest.name: BreakoutRetest,
    IctSweep.name: IctSweep,
}


def build_strategy(name: str, params: dict | None = None, rr: float = 3.0) -> StrategyBase:
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy {name!r}. Choose from: {', '.join(STRATEGIES)}")
    return STRATEGIES[name](rr=rr, **(params or {}))


__all__ = ["Signal", "StrategyBase", "BreakoutRetest", "IctSweep", "STRATEGIES", "build_strategy"]
