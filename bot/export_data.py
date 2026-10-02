"""Export MT5 history to CSV for backtesting (Windows, MT5 terminal running).

    python -m bot.export_data --symbol EURUSD --timeframe M15 --bars 50000
"""

import argparse
from pathlib import Path

from .broker.mt5_broker import MT5Broker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--bars", type=int, default=50_000)
    parser.add_argument("--out", help="default: data/<SYMBOL>_<TF>.csv")
    args = parser.parse_args()

    broker = MT5Broker()
    broker.connect()
    try:
        df = broker.rates(args.symbol, args.timeframe, args.bars)
    finally:
        broker.shutdown()
    out = Path(args.out or f"data/{args.symbol}_{args.timeframe}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} bars to {out}")


if __name__ == "__main__":
    main()
