"""Live / paper trading loop.

    python -m bot.run --config config/config.yaml

Every poll it checks each symbol for a newly closed candle, runs the strategy on the
latest `bars` candles and acts only on a signal from that newest candle. Before any
order it checks: one position per symbol, max open positions, news blackout and the
FundedNext guard (daily loss / max loss including the new trade's full risk).
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from pathlib import Path

import pandas as pd

from .broker import Broker, Position
from .config import load_config, prop_rules_from_config, strategy_from_config
from .data import align
from .news import NewsFilter
from .risk import PropGuard, position_size
from .strategies import Signal, StrategyBase

log = logging.getLogger("bot")


class Runner:
    def __init__(self, cfg: dict, broker: Broker, strategy: StrategyBase, guard: PropGuard, news: NewsFilter):
        self.cfg = cfg
        self.broker = broker
        self.strategy = strategy
        self.guard = guard
        self.news = news
        self.live = cfg["mode"] == "live"
        self.risk_cfg = cfg["risk"]
        self.utc_offset = pd.Timedelta(hours=float(cfg.get("server_utc_offset_hours", 0)))
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.halted_day: str | None = None

    def open_risk(self, positions: list[Position]) -> float:
        total = 0.0
        for p in positions:
            spec = self.broker.symbol_spec(p.symbol)
            if p.sl:
                loss_to_sl = (p.price_open - p.sl) if p.side == "buy" else (p.sl - p.price_open)
                total += max(0.0, loss_to_sl) * p.volume * spec.value_per_price_unit
            else:  # no stop (e.g. a manual trade): assume it can lose all remaining room
                total += math.inf
        return total

    def step(self) -> None:
        symbols = self.cfg["symbols"]
        server_now = self.broker.server_time(symbols[0])
        day = server_now.strftime("%Y-%m-%d")
        acct = self.broker.account()
        self.guard.update(day, acct.balance, acct.equity)

        reason = self.guard.limit_hit(acct.equity)
        if reason:
            if self.halted_day != day:
                log.error("RISK LIMIT: %s. No new trades today.", reason)
                self.halted_day = day
                if self.live and self.cfg["prop"].get("flatten_on_limit", True):
                    for p in self.broker.positions(own_only=True):
                        log.error("Flattening %s %s %.2f", p.symbol, p.side, p.volume)
                        self.broker.close_position(p)
            return

        for symbol in symbols:
            try:
                self.check_symbol(symbol, server_now)
            except Exception:
                log.exception("Error on %s", symbol)

    def check_symbol(self, symbol: str, server_now: pd.Timestamp) -> None:
        tf = self.cfg["timeframe"]
        df = self.broker.rates(symbol, tf, int(self.cfg["bars"]))
        newest = df["time"].iat[-1]
        if self.last_bar.get(symbol) == newest:
            return
        self.last_bar[symbol] = newest

        correlated = None
        if self.strategy.uses_correlated:
            corr_symbol = self.cfg.get("smt_pairs", {}).get(symbol)
            if not corr_symbol:
                log.warning("%s: no smt_pairs entry, skipping", symbol)
                return
            correlated = align(df, self.broker.rates(corr_symbol, tf, int(self.cfg["bars"])))

        signals = self.strategy.find_signals(df, correlated)
        if not signals or signals[-1].index != len(df) - 1:
            return
        self.execute(symbol, signals[-1], server_now)

    def execute(self, symbol: str, sig: Signal, server_now: pd.Timestamp) -> None:
        log.info("SIGNAL %s %s %s entry~%.5f sl=%.5f (%s)", sig.strategy, symbol, sig.side, sig.entry, sig.sl, sig.note)

        all_positions = self.broker.positions(own_only=False)  # manual trades use the same loss room
        positions = [p for p in all_positions if p.magic == self.broker.magic]
        if any(p.symbol == symbol for p in positions):
            log.info("skip: already in a %s position", symbol)
            return
        if len(positions) >= int(self.risk_cfg["max_open_positions"]):
            log.info("skip: max open positions reached")
            return
        blocked = self.news.blocked(symbol, server_now - self.utc_offset)
        if blocked:
            log.info("skip: news blackout (%s)", blocked)
            return

        bid, ask = self.broker.quote(symbol)
        price = ask if sig.side == "buy" else bid
        stop_distance = price - sig.sl if sig.side == "buy" else sig.sl - price
        spec = self.broker.symbol_spec(symbol)
        if stop_distance <= max(spec.stops_level_points, 1) * spec.point:
            log.info("skip: price %.5f already too close to / beyond the stop %.5f", price, sig.sl)
            return
        rr = self.strategy.rr
        tp = price + rr * stop_distance if sig.side == "buy" else price - rr * stop_distance

        acct = self.broker.account()
        lots = position_size(acct.balance, float(self.risk_cfg["risk_per_trade_pct"]), price, sig.sl,
                             spec.value_per_price_unit, spec.vol_min, spec.vol_max, spec.vol_step)
        if lots <= 0:
            log.info("skip: stop too wide for the minimum lot at %.2f%% risk", self.risk_cfg["risk_per_trade_pct"])
            return
        commission = lots * float(self.risk_cfg.get("commission_per_lot", 0))
        new_risk = stop_distance * lots * spec.value_per_price_unit + commission
        ok, why = self.guard.can_open(acct.equity, self.open_risk(all_positions), new_risk)
        if not ok:
            log.info("skip: guard: %s", why)
            return

        if not self.live:
            log.info("PAPER %s %s %.2f lots @ %.5f sl=%.5f tp=%.5f risk=%.2f %s",
                     sig.side, symbol, lots, price, sig.sl, tp, new_risk, acct.currency)
            return
        ticket = self.broker.market_order(symbol, sig.side, lots, sig.sl, tp, comment=sig.strategy)
        log.info("ORDER #%s %s %s %.2f lots @ ~%.5f sl=%.5f tp=%.5f risk=%.2f %s",
                 ticket, sig.side, symbol, lots, price, sig.sl, tp, new_risk, acct.currency)


def setup_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def main() -> None:
    parser = argparse.ArgumentParser(description="FundedNext MT5 trading bot")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.get("log_file"))
    strategy = strategy_from_config(cfg)
    guard = PropGuard(prop_rules_from_config(cfg), cfg.get("state_file"))
    if cfg["mode"] == "live":
        guard.check_ea_allowed()
    news_cfg = cfg.get("news", {})
    news = NewsFilter(news_cfg.get("file"), news_cfg.get("minutes_before", 10), news_cfg.get("minutes_after", 10))

    from .broker.mt5_broker import MT5Broker
    mt5_cfg, risk_cfg = cfg["mt5"], cfg["risk"]
    broker = MT5Broker(mt5_cfg.get("login", 0), mt5_cfg.get("password", ""), mt5_cfg.get("server", ""),
                       mt5_cfg.get("path", ""), int(risk_cfg["magic"]), int(risk_cfg["deviation_points"]))
    broker.connect()
    log.info("Running %s on %s %s in %s mode", strategy.name, cfg["symbols"], cfg["timeframe"], cfg["mode"].upper())

    runner = Runner(cfg, broker, strategy, guard, news)
    try:
        while True:
            try:
                runner.step()
            except Exception:
                log.exception("step failed")
            time.sleep(float(cfg.get("poll_seconds", 5)))
    except KeyboardInterrupt:
        log.info("Stopped")
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
