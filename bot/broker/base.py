from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass
class Account:
    balance: float
    equity: float
    currency: str


@dataclass
class SymbolSpec:
    name: str
    digits: int
    point: float
    value_per_price_unit: float   # P/L of 1 lot for a 1.0 price move, account currency
    vol_min: float
    vol_max: float
    vol_step: float
    stops_level_points: int


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str          # "buy" / "sell"
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int = 0


class Broker(Protocol):
    magic: int

    def connect(self) -> None: ...
    def shutdown(self) -> None: ...
    def rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame: ...
    def account(self) -> Account: ...
    def symbol_spec(self, symbol: str) -> SymbolSpec: ...
    def quote(self, symbol: str) -> tuple[float, float]: ...
    def server_time(self, symbol: str) -> pd.Timestamp: ...
    def positions(self, symbol: str | None = None, own_only: bool = True) -> list[Position]: ...
    def market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float, comment: str) -> int: ...
    def close_position(self, position: Position) -> None: ...
