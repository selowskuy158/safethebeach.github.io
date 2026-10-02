"""Account-level backtest: the same strategy code, position sizing and FundedNext
guard as the live bot, on a simulated $ account.

    python -m bot.backtest --csv data/EURUSD_M15.csv data/GBPUSD_M15.csv

Model (deliberately conservative):
  - Prices in the CSVs are bid. A signal on candle i's close fills at candle i+1's
    open. Buys pay the spread on entry, sells on exit; slippage is added to every
    entry and every stop-loss exit. A candle that gaps through the stop fills at
    its open (worse than the stop).
  - Lots from the account balance x risk %, rounded down to 0.01; trades whose
    minimum lot would over-risk are skipped. Commission per lot is paid on open.
  - Stop loss at the signal's level; take profit re-set to rr x the real stop
    distance from the fill (as the live bot does).
  - If the stop and the target are both inside one candle, the stop counts first.
  - The PropGuard blocks trades exactly as live (one per symbol, max open
    positions, full-stop risk vs the trailing max loss and personal daily stop).
  - Breaches are checked on each candle's worst price, not just its close.
Then a Monte Carlo reshuffles the trade results to estimate, at several risk
levels, how often the account reaches the profit target before the max loss.
"""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import prop_rules_from_config, strategy_from_config
from .data import align, load_csv, pip_size
from .risk import PropGuard, position_size
from .strategies import STRATEGIES, StrategyBase

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.example.yaml"


def value_per_price_unit(symbol: str, price: float, contract_size: float) -> float:
    """USD P/L of 1 lot for a 1.0 price move (USD account)."""
    s = "".join(ch for ch in symbol.upper() if ch.isalpha())[:6]
    if s.endswith("USD"):
        return contract_size
    if s.startswith("USD"):
        return contract_size / price
    raise ValueError(f"{symbol}: the backtest supports XXXUSD and USDXXX pairs only (USD account)")


@dataclass
class Trade:
    symbol: str
    side: str
    lots: float
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    risk_money: float      # planned loss at the stop, including commission
    commission: float
    note: str
    entry_row: int
    exit_time: pd.Timestamp | None = None
    exit: float = 0.0
    pnl: float = 0.0       # net of commission
    r: float = 0.0
    reason: str = ""


@dataclass
class AccountResult:
    strategy: str
    initial: float
    trades: list[Trade]
    equity: pd.Series
    blocked: dict = field(default_factory=dict)
    breached_at: pd.Timestamp | None = None
    breach_reason: str = ""
    min_room: float = float("inf")
    halted_days: int = 0


def simulate_account(frames: dict[str, pd.DataFrame], strategy: StrategyBase, cfg: dict,
                     risk_pct: float | None = None, spread_pips: float | None = None,
                     traded: list[str] | None = None) -> AccountResult:
    """frames: every loaded symbol (SMT pairs included). traded: symbols that place trades."""
    risk_cfg, bt = cfg["risk"], cfg.get("backtest", {})
    rules = dataclasses.replace(prop_rules_from_config(cfg), high_water_balance=0.0)
    guard = PropGuard(rules)
    risk_pct = float(risk_cfg["risk_per_trade_pct"]) if risk_pct is None else risk_pct
    max_open = int(risk_cfg["max_open_positions"])
    commission = float(risk_cfg.get("commission_per_lot", 0))
    contract = float(bt.get("contract_size", 100_000))
    flatten = cfg["prop"].get("flatten_on_limit", True)
    spreads = bt.get("spread_pips", {}) or {}

    sym_data, entries = {}, {}
    for sym in traded or list(frames):
        df = frames[sym]
        pip = pip_size(sym)
        sp = spread_pips if spread_pips is not None else float(spreads.get(sym, spreads.get("default", 1.0)))
        sym_data[sym] = {
            "o": df["open"].to_numpy(), "h": df["high"].to_numpy(), "l": df["low"].to_numpy(),
            "c": df["close"].to_numpy(), "t": df["time"],
            "row": dict(zip(df["time"], range(len(df)))),
            "spread": sp * pip, "slip": float(bt.get("slippage_pips", 0.0)) * pip,
        }
        correlated = None
        if strategy.uses_correlated:
            pair = cfg.get("smt_pairs", {}).get(sym)
            if pair not in frames:
                raise ValueError(f"{strategy.name} needs the SMT pair {pair!r} for {sym}: pass its CSV too")
            correlated = align(df, frames[pair])
        entries[sym] = {}
        for sig in strategy.find_signals(df, correlated):
            entries[sym].setdefault(sig.index + 1, sig)

    balance = rules.initial_balance
    open_trades: dict[str, Trade] = {}
    last_close: dict[str, float] = {}
    closed: list[Trade] = []
    curve_t, curve_v = [], []
    blocked = {"guard": 0, "min_lot": 0, "max_open": 0, "in_position": 0, "gap_past_stop": 0, "halted": 0}
    result = AccountResult(strategy.name, rules.initial_balance, closed, pd.Series(dtype=float), blocked)
    halted_day = None

    def floating(tr: Trade, bid: float) -> float:
        d = sym_data[tr.symbol]
        px = bid if tr.side == "buy" else bid + d["spread"]
        move = px - tr.entry if tr.side == "buy" else tr.entry - px
        return move * tr.lots * value_per_price_unit(tr.symbol, px, contract)

    def close(tr: Trade, price: float, when, reason: str) -> None:
        nonlocal balance
        move = price - tr.entry if tr.side == "buy" else tr.entry - price
        gross = move * tr.lots * value_per_price_unit(tr.symbol, price, contract)
        balance += gross
        tr.exit_time, tr.exit, tr.reason = when, price, reason
        tr.pnl = gross - tr.commission
        tr.r = tr.pnl / tr.risk_money
        closed.append(tr)
        del open_trades[tr.symbol]

    timeline = sorted(set().union(*[set(d["row"]) for d in sym_data.values()]))
    for t in timeline:
        day = t.strftime("%Y-%m-%d")
        equity = balance + sum(floating(tr, last_close[s]) for s, tr in open_trades.items())
        guard.update(day, balance, equity)
        worst = {}

        for sym, d in sym_data.items():
            r = d["row"].get(t)
            if r is None:
                continue
            o, h, lo, c = d["o"][r], d["h"][r], d["l"][r], d["c"][r]
            spread, slip = d["spread"], d["slip"]

            sig = entries[sym].get(r)
            if sig is not None:
                if halted_day == day:
                    blocked["halted"] += 1
                elif sym in open_trades:
                    blocked["in_position"] += 1
                elif len(open_trades) >= max_open:
                    blocked["max_open"] += 1
                else:
                    buy = sig.side == "buy"
                    entry = o + spread + slip if buy else o - slip
                    dist = entry - sig.sl if buy else sig.sl - entry
                    if dist <= 0:
                        blocked["gap_past_stop"] += 1
                    else:
                        vpu = value_per_price_unit(sym, entry, contract)
                        lots = position_size(balance, risk_pct, entry, sig.sl, vpu, 0.01, 100.0, 0.01)
                        if lots <= 0:
                            blocked["min_lot"] += 1
                        else:
                            comm = lots * commission
                            risk_money = dist * lots * vpu + comm
                            open_risk = sum(abs(tr.entry - tr.sl) * tr.lots *
                                            value_per_price_unit(tr.symbol, tr.entry, contract)
                                            for tr in open_trades.values())
                            ok, _ = guard.can_open(equity, open_risk, risk_money)
                            if not ok:
                                blocked["guard"] += 1
                            else:
                                tp = entry + strategy.rr * dist if buy else entry - strategy.rr * dist
                                balance -= comm
                                open_trades[sym] = Trade(sym, sig.side, lots, t, entry, sig.sl, tp,
                                                         risk_money, comm, sig.note, r)

            tr = open_trades.get(sym)
            if tr is not None:
                first_bar = r == tr.entry_row
                if tr.side == "buy":
                    if lo <= tr.sl:
                        fill = o if (o < tr.sl and not first_bar) else tr.sl
                        close(tr, fill - slip, t, "sl")
                    elif h >= tr.tp:
                        close(tr, tr.tp, t, "tp")
                else:
                    ask_o, ask_h, ask_l = o + spread, h + spread, lo + spread
                    if ask_h >= tr.sl:
                        fill = ask_o if (ask_o > tr.sl and not first_bar) else tr.sl
                        close(tr, fill + slip, t, "sl")
                    elif ask_l <= tr.tp:
                        close(tr, tr.tp, t, "tp")
            last_close[sym] = c
            worst[sym] = lo if sym in open_trades and open_trades[sym].side == "buy" else h

        eq_worst = balance + sum(floating(tr, worst.get(s, last_close[s])) for s, tr in open_trades.items())
        floor = guard.max_loss_floor()
        result.min_room = min(result.min_room, eq_worst - floor)
        firm_daily = rules.initial_balance * rules.daily_loss_pct / 100.0
        if eq_worst < floor or (rules.daily_loss_pct > 0 and guard.daily_loss(eq_worst) > firm_daily):
            result.breached_at = t
            result.breach_reason = (f"equity {eq_worst:.2f} < max-loss floor {floor:.2f}" if eq_worst < floor
                                    else f"daily loss {guard.daily_loss(eq_worst):.2f} > {firm_daily:.2f}")
            for s, tr in list(open_trades.items()):
                close(tr, worst.get(s, last_close[s]), t, "breach")
            curve_t.append(t)
            curve_v.append(balance)
            break

        equity = balance + sum(floating(tr, last_close[s]) for s, tr in open_trades.items())
        guard.update(day, balance, equity)
        if guard.limit_hit(equity) and halted_day != day:
            halted_day = day
            result.halted_days += 1
            if flatten:
                for s, tr in list(open_trades.items()):
                    d = sym_data[s]
                    close(tr, last_close[s] - d["slip"] if tr.side == "buy" else last_close[s] + d["spread"] + d["slip"],
                          t, "guard_flatten")
                equity = balance
        curve_t.append(t)
        curve_v.append(equity)

    for s, tr in list(open_trades.items()):
        close(tr, last_close[s], timeline[-1], "end_of_data")
    result.equity = pd.Series(curve_v, index=curve_t)
    return result


def summarize(res: AccountResult, target_pct: float) -> dict:
    trades, eq, init = res.trades, res.equity, res.initial
    out = {"trades": len(trades)}
    if not trades:
        out["breached"] = res.breached_at is not None
        return out
    pnl = pd.Series([t.pnl for t in trades])
    rs = pd.Series([t.r for t in trades])
    peak = eq.cummax()
    streak = worst_streak = 0
    for t in trades:
        streak = streak + 1 if t.pnl < 0 else 0
        worst_streak = max(worst_streak, streak)
    reached = eq[eq >= init * (1 + target_pct / 100.0)]
    span_days = max((eq.index[-1] - eq.index[0]).days, 1)
    out.update({
        "period": f"{eq.index[0]:%Y-%m-%d} -> {eq.index[-1]:%Y-%m-%d}",
        "trades_per_month": round(len(trades) / span_days * 30.4, 1),
        "win_rate_%": round((pnl > 0).mean() * 100, 1),
        "avg_R": round(rs.mean(), 3),
        "profit_factor": round(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum(), 2) if (pnl < 0).any() else float("inf"),
        "net_profit_$": round(pnl.sum(), 2),
        "final_balance_$": round(init + pnl.sum(), 2),
        "return_%": round(pnl.sum() / init * 100, 2),
        "max_drawdown_$": round((peak - eq).max(), 2),
        "max_drawdown_%": round(((peak - eq) / peak).max() * 100, 2),
        "max_losing_streak": worst_streak,
        "commission_$": round(sum(t.commission for t in trades), 2),
        "avg_lots": round(float(np.mean([t.lots for t in trades])), 3),
        "closest_to_floor_$": round(res.min_room, 2),
        "breached": f"YES {res.breach_reason} @ {res.breached_at:%Y-%m-%d %H:%M}" if res.breached_at is not None else "no",
        f"reached_+{target_pct:g}%": f"{reached.index[0]:%Y-%m-%d}" if len(reached) else "no",
        "guard_halted_days": res.halted_days,
        "skipped_by_guard": res.blocked["guard"] + res.blocked["halted"],
        "skipped_min_lot": res.blocked["min_lot"],
        "skipped_busy": res.blocked["in_position"] + res.blocked["max_open"],
    })
    return out


def monte_carlo(rs: list[float], risk_pcts: list[float], max_loss_pct: float, trailing: bool,
                target_pct: float, buffer: float, sims: int = 5000, max_trades: int = 500,
                seed: int = 7) -> pd.DataFrame:
    """Resample trade R-multiples: P(target before the max-loss floor) per risk level."""
    rng = np.random.default_rng(seed)
    r_arr = np.asarray(rs, dtype=float)
    limit, target = max_loss_pct / 100.0, 1 + target_pct / 100.0
    rows = {}
    for risk in risk_pcts:
        passed = failed = 0
        trades_to_pass = []
        for _ in range(sims):
            bal = hwm = 1.0
            for n, r in enumerate(rng.choice(r_arr, size=max_trades), start=1):
                bal += bal * risk / 100.0 * r
                hwm = max(hwm, bal)
                floor = min(hwm - limit, 1.0) if trailing else 1.0 - limit
                if bal >= target:
                    passed += 1
                    trades_to_pass.append(n)
                    break
                if bal - bal * risk / 100.0 < floor + (1 - buffer) * limit:
                    failed += 1  # the guard can't fit another full-risk trade: account is done
                    break
        rows[f"{risk:g}%"] = {
            f"P(+{target_pct:g}% first)": f"{passed / sims:.0%}",
            "P(max loss first)": f"{failed / sims:.0%}",
            "P(neither in 500 trades)": f"{(sims - passed - failed) / sims:.0%}",
            "median trades to target": int(np.median(trades_to_pass)) if trades_to_pass else "-",
        }
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Account-level backtest with FundedNext risk rules")
    parser.add_argument("--csv", nargs="+", required=True,
                        help="OHLC CSVs named <SYMBOL>_<TF>.csv (include SMT pairs for ict_sweep)")
    parser.add_argument("--symbols", nargs="+", help="symbols to trade (default: every CSV)")
    parser.add_argument("--strategy", default="all", help=f"one of {list(STRATEGIES)} or 'all'")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--risk-pct", type=float, help="default: risk.risk_per_trade_pct")
    parser.add_argument("--spread-pips", type=float, help="override every symbol's spread")
    parser.add_argument("--start", help="only bars from this date (e.g. split in- vs out-of-sample)")
    parser.add_argument("--end", help="only bars up to this date")
    parser.add_argument("--target-pct", type=float, default=8.0, help="profit target (Instant scale-up: 8)")
    parser.add_argument("--mc-risk", default="0.25,0.5,1.0", help="risk levels for the Monte Carlo")
    parser.add_argument("--mc-sims", type=int, default=5000)
    parser.add_argument("--trades-out", help="write trades to <name>_<strategy>.csv")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    frames = {}
    for path in args.csv:
        df = load_csv(path)
        if args.start:
            df = df[df["time"] >= pd.Timestamp(args.start)]
        if args.end:
            df = df[df["time"] <= pd.Timestamp(args.end)]
        frames[Path(path).stem.split("_")[0].upper()] = df.reset_index(drop=True)
    traded = [s.upper() for s in args.symbols] if args.symbols else list(frames)

    risk = args.risk_pct if args.risk_pct is not None else float(cfg["risk"]["risk_per_trade_pct"])
    prop = cfg["prop"]
    print(f"Account ${prop['initial_balance']:,} | risk {risk}%/trade | max loss {prop['max_loss_pct']}%"
          f"{' trailing' if prop.get('trailing_max_loss') else ''} | daily limit {prop['daily_loss_pct'] or 'none'}"
          f" | personal daily stop {prop.get('personal_daily_stop_pct') or 'off'}% | "
          f"commission ${cfg['risk'].get('commission_per_lot', 0)}/lot | symbols {traded}\n")

    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    variants = []
    for name in names:
        variants.append((name, name, {}))
        if name == "ict_sweep":  # SMT is optional: test the setup with and without it
            pairs_loaded = all(cfg.get("smt_pairs", {}).get(s) in frames for s in traded)
            smt_on = bool((cfg.get("strategy_params") or {}).get(name, {}).get("smt"))
            if pairs_loaded:
                variants.append((f"{name}+smt" if not smt_on else f"{name}-nosmt", name, {"smt": not smt_on}))
    summaries, all_rs = {}, {}
    for label, name, overrides in variants:
        strategy = strategy_from_config(cfg, name, **overrides)
        needed = set(traded)
        if strategy.uses_correlated:
            needed |= {cfg.get("smt_pairs", {}).get(s) for s in traded}
        if not needed <= set(frames):
            print(f"{label}: skipped, missing CSVs for {sorted(str(x) for x in needed - set(frames))}")
            continue
        res = simulate_account(frames, strategy, cfg, risk, args.spread_pips, traded)
        summaries[label] = summarize(res, args.target_pct)
        all_rs[label] = [t.r for t in res.trades if t.reason != "end_of_data"]
        if args.trades_out and res.trades:
            out = Path(args.trades_out)
            out = out.with_name(f"{out.stem}_{label}.csv")
            pd.DataFrame([dataclasses.asdict(t) for t in res.trades]).to_csv(out, index=False)
            print(f"{label}: {len(res.trades)} trades written to {out}")

    if not summaries:
        return
    print(pd.DataFrame(summaries).to_string())
    risks = [float(x) for x in args.mc_risk.split(",")]
    for name, rs in all_rs.items():
        if len(rs) < 30:
            print(f"\n{name}: only {len(rs)} trades, too few for a meaningful Monte Carlo")
            continue
        print(f"\nMonte Carlo, {name} ({len(rs)} trades resampled, {args.mc_sims} runs, "
              f"{prop['max_loss_pct']}% {'trailing ' if prop.get('trailing_max_loss') else ''}max loss, "
              f"guard buffer {prop.get('safety_buffer', 0.8)}):")
        print(monte_carlo(rs, risks, float(prop["max_loss_pct"]), bool(prop.get("trailing_max_loss")),
                          args.target_pct, float(prop.get("safety_buffer", 0.8)), args.mc_sims).to_string())


if __name__ == "__main__":
    main()
