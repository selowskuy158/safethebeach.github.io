from __future__ import annotations

import os
from pathlib import Path

import yaml

from .risk import PropRules
from .strategies import StrategyBase, build_strategy


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    mt5 = cfg.setdefault("mt5", {})
    if os.environ.get("MT5_LOGIN"):
        mt5["login"] = int(os.environ["MT5_LOGIN"])
    if os.environ.get("MT5_PASSWORD"):
        mt5["password"] = os.environ["MT5_PASSWORD"]
    if os.environ.get("MT5_SERVER"):
        mt5["server"] = os.environ["MT5_SERVER"]
    if cfg.get("mode") not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    return cfg


def strategy_from_config(cfg: dict, name: str | None = None, **overrides) -> StrategyBase:
    name = name or cfg["strategy"]
    params = dict((cfg.get("strategy_params") or {}).get(name) or {}) | overrides
    params.setdefault("session_utc_offset_hours", float(cfg.get("server_utc_offset_hours", 0)))
    return build_strategy(name, params, rr=float(cfg.get("rr", 3.0)))


def prop_rules_from_config(cfg: dict) -> PropRules:
    p = dict(cfg["prop"])
    p.pop("flatten_on_limit", None)
    return PropRules(**p)
