"""Position sizing and FundedNext prop-firm guard rails.

FundedNext rules this mirrors (check yours with the FundedNext MCP tool
get_account_applicable_rules, they differ per plan):
  - Stellar Instant: NO daily loss limit; 6% *trailing* maximum loss (trails the
    highest balance, capped at the initial balance); 3% risk limit per trade idea;
    $7/lot forex commission charged on open.
  - Stellar 2-Step: 5% daily loss limit (of initial balance, running + closed),
    10% static maximum loss. Stellar 1-Step: 3% daily, 6% max.
  - EAs/bots are only allowed on MT4/MT5 accounts below $50,000.
The guard stops *before* a limit: a new trade is blocked if its full stop-loss
risk, on top of every open position's, could take losses past safety_buffer x the
firm limit (or past your own personal_daily_stop_pct).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


def position_size(balance: float, risk_pct: float, entry: float, sl: float,
                  value_per_price_unit: float, vol_min: float, vol_max: float, vol_step: float) -> float:
    """Lots so that hitting `sl` loses `risk_pct`% of `balance`.

    value_per_price_unit: account-currency P/L of 1.0 lot for a 1.0 price move
    (MT5: trade_tick_value / trade_tick_size). Rounds *down* to vol_step and
    returns 0.0 when even the minimum volume would risk more than asked.
    """
    distance = abs(entry - sl)
    if distance <= 0 or value_per_price_unit <= 0:
        return 0.0
    raw = balance * risk_pct / 100.0 / (distance * value_per_price_unit)
    lots = math.floor(raw / vol_step + 1e-9) * vol_step
    if lots < vol_min:
        return 0.0
    return round(min(lots, vol_max), 8)


@dataclass
class PropRules:
    initial_balance: float
    daily_loss_pct: float = 0.0          # firm daily loss limit, 0 = none (Stellar Instant)
    max_loss_pct: float = 6.0
    trailing_max_loss: bool = True
    safety_buffer: float = 0.8
    personal_daily_stop_pct: float = 0.0  # your own daily stop (of initial balance), 0 = off
    risk_limit_pct: float = 3.0           # firm max risk per trade idea, 0 = none
    ea_max_account_size: float = 50_000.0
    high_water_balance: float = 0.0       # seed for the trailing max loss (see README)

    @property
    def daily_limit(self) -> float:
        """Daily loss (money) at which the guard stops trading; inf if none."""
        limits = []
        if self.daily_loss_pct > 0:
            limits.append(self.safety_buffer * self.initial_balance * self.daily_loss_pct / 100.0)
        if self.personal_daily_stop_pct > 0:
            limits.append(self.initial_balance * self.personal_daily_stop_pct / 100.0)
        return min(limits) if limits else math.inf

    @property
    def max_limit(self) -> float:
        return self.initial_balance * self.max_loss_pct / 100.0


@dataclass
class GuardState:
    day: str = ""
    day_start: float = 0.0          # max(balance, equity) at the start of the server day
    high_water_balance: float = 0.0


class PropGuard:
    def __init__(self, rules: PropRules, state_path: str | Path | None = None):
        self.rules = rules
        self.state_path = Path(state_path) if state_path else None
        self.state = self._load()
        self.state.high_water_balance = max(self.state.high_water_balance, rules.high_water_balance,
                                            rules.initial_balance)

    def check_ea_allowed(self) -> None:
        if self.rules.initial_balance >= self.rules.ea_max_account_size:
            raise RuntimeError(
                f"FundedNext does not allow EAs/bots on accounts of ${self.rules.ea_max_account_size:,.0f} "
                f"or more (initial balance ${self.rules.initial_balance:,.0f}). Run in paper mode and trade manually."
            )

    def update(self, day: str, balance: float, equity: float) -> None:
        """Call every loop with the broker server date (YYYY-MM-DD)."""
        if day != self.state.day:
            self.state.day = day
            self.state.day_start = max(balance, equity)
        self.state.high_water_balance = max(self.state.high_water_balance, balance)
        self._save()

    def max_loss_floor(self) -> float:
        """Equity level at which the firm breaches the account."""
        r = self.rules
        if r.trailing_max_loss:
            return min(self.state.high_water_balance - r.max_limit, r.initial_balance)
        return r.initial_balance - r.max_limit

    def stop_level(self) -> float:
        """Equity level the guard refuses to risk going below (floor + unused buffer)."""
        return self.max_loss_floor() + (1 - self.rules.safety_buffer) * self.rules.max_limit

    def daily_loss(self, equity: float) -> float:
        return max(0.0, self.state.day_start - equity)

    def limit_hit(self, equity: float) -> str | None:
        """Reason string if losses already reached a guard level (flatten + stop for the day)."""
        if self.daily_loss(equity) >= self.rules.daily_limit:
            return f"daily loss {self.daily_loss(equity):.2f} >= daily stop {self.rules.daily_limit:.2f}"
        if equity <= self.stop_level():
            return f"equity {equity:.2f} <= guard level {self.stop_level():.2f} (firm floor {self.max_loss_floor():.2f})"
        return None

    def can_open(self, equity: float, open_risk: float, new_risk: float) -> tuple[bool, str]:
        """open_risk: money lost if every open position hits its stop. new_risk: same for the new trade."""
        r = self.rules
        if r.risk_limit_pct > 0 and new_risk > r.initial_balance * r.risk_limit_pct / 100.0:
            return False, f"trade risk {new_risk:.2f} exceeds the firm's {r.risk_limit_pct}% risk limit"
        worst_daily = self.daily_loss(equity) + open_risk + new_risk
        if worst_daily > r.daily_limit:
            return False, f"daily loss could reach {worst_daily:.2f} > daily stop {r.daily_limit:.2f}"
        worst_equity = equity - open_risk - new_risk
        if worst_equity < self.stop_level():
            return False, f"equity could fall to {worst_equity:.2f}, below guard level {self.stop_level():.2f}"
        return True, "ok"

    def _load(self) -> GuardState:
        if self.state_path and self.state_path.exists():
            return GuardState(**json.loads(self.state_path.read_text()))
        return GuardState()

    def _save(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(asdict(self.state), indent=2))
