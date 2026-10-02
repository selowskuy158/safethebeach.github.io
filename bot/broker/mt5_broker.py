"""MetaTrader 5 adapter (official `MetaTrader5` package: Windows + a running MT5 terminal)."""

from __future__ import annotations

import logging

import pandas as pd

from .base import Account, Position, SymbolSpec

log = logging.getLogger(__name__)

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


class MT5Broker:
    def __init__(self, login: int = 0, password: str = "", server: str = "", path: str = "",
                 magic: int = 150820, deviation_points: int = 20):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError(
                "The MetaTrader5 package is missing. It only runs on Windows: pip install MetaTrader5"
            ) from exc
        self.mt5 = mt5
        self.login, self.password, self.server, self.path = login, password, server, path
        self.magic = magic
        self.deviation = deviation_points

    def connect(self) -> None:
        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=int(self.login), password=self.password, server=self.server)
        if not self.mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {self.mt5.last_error()}")
        info = self.mt5.account_info()
        log.info("Connected to MT5 account %s on %s (%s)", info.login, info.server, info.currency)

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def _ensure_symbol(self, symbol: str):
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Unknown symbol {symbol}")
        if not info.visible and not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Cannot select {symbol} in Market Watch")
        return info

    def rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Closed bars only (start_pos=1 skips the candle still forming), times in server time."""
        self._ensure_symbol(symbol)
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        data = self.mt5.copy_rates_from_pos(symbol, tf, 1, count)
        if data is None or len(data) == 0:
            raise RuntimeError(f"No rates for {symbol} {timeframe}: {self.mt5.last_error()}")
        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close"]]

    def account(self) -> Account:
        a = self.mt5.account_info()
        return Account(balance=a.balance, equity=a.equity, currency=a.currency)

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        i = self._ensure_symbol(symbol)
        return SymbolSpec(
            name=symbol, digits=i.digits, point=i.point,
            value_per_price_unit=i.trade_tick_value / i.trade_tick_size,
            vol_min=i.volume_min, vol_max=i.volume_max, vol_step=i.volume_step,
            stops_level_points=i.trade_stops_level,
        )

    def quote(self, symbol: str) -> tuple[float, float]:
        t = self.mt5.symbol_info_tick(symbol)
        return t.bid, t.ask

    def server_time(self, symbol: str) -> pd.Timestamp:
        return pd.to_datetime(self.mt5.symbol_info_tick(symbol).time, unit="s")

    def positions(self, symbol: str | None = None, own_only: bool = True) -> list[Position]:
        """The bot's positions (by magic number), or every position with own_only=False."""
        raw = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        out = []
        for p in raw or []:
            if own_only and p.magic != self.magic:
                continue
            side = "buy" if p.type == self.mt5.POSITION_TYPE_BUY else "sell"
            out.append(Position(p.ticket, p.symbol, side, p.volume, p.price_open, p.sl, p.tp, p.profit, p.magic))
        return out

    def _filling(self, symbol: str) -> int:
        mode = self.mt5.symbol_info(symbol).filling_mode
        if mode & self.mt5.SYMBOL_FILLING_FOK:
            return self.mt5.ORDER_FILLING_FOK
        if mode & self.mt5.SYMBOL_FILLING_IOC:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict):
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            detail = result._asdict() if result is not None else self.mt5.last_error()
            raise RuntimeError(f"order_send failed: {detail}")
        return result

    def market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float, comment: str) -> int:
        info = self._ensure_symbol(symbol)
        bid, ask = self.quote(symbol)
        is_buy = side == "buy"
        result = self._send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
            "price": ask if is_buy else bid,
            "sl": round(sl, info.digits),
            "tp": round(tp, info.digits),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        })
        return result.order

    def close_position(self, position: Position) -> None:
        bid, ask = self.quote(position.symbol)
        is_buy = position.side == "buy"
        self._send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": bid if is_buy else ask,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "flatten",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(position.symbol),
        })
