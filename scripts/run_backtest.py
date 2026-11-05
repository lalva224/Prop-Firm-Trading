#!/usr/bin/env python3
"""Run the trend system over a universe of free daily data.

    python scripts/run_backtest.py --universe etf --start 2005-01-01
    python scripts/run_backtest.py --universe futures --target-vol 0.15
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from tcs import metrics
from tcs.backtest import BacktestConfig, run
from tcs.data import load_prices
from tcs.signals import ewmac_blend, trend_signal
from tcs.universe import UNIVERSES


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default="etf", choices=["etf", "futures"])
    p.add_argument("--start", default="2005-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--target-vol", type=float, default=0.12)
    p.add_argument("--cost-bps", type=float, default=2.0)
    p.add_argument("--signal", default="ewmac", choices=["ewmac", "lookback"])
    p.add_argument("--lookbacks", default="63,252")
    p.add_argument("--smooth", type=int, default=10)
    p.add_argument("--buffer", type=float, default=0.30)
    p.add_argument("--max-leverage", type=float, default=6.0)
    p.add_argument("--trials", type=int, default=9,
                   help="total parameter variants tried, for the noise bar")
    p.add_argument("--refresh", action="store_true", help="re-download prices")
    p.add_argument("--out", default="out")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tickers = list(UNIVERSES[args.universe])
    prices = load_prices(
        tickers,
        start=args.start,
        end=args.end,
        cache_key=f"{args.universe}_{args.start}",
        refresh=args.refresh,
    )
    print(f"\nloaded {prices.shape[1]} instruments, "
          f"{prices.index[0].date()} -> {prices.index[-1].date()}")

    lookbacks = tuple(int(x) for x in args.lookbacks.split(","))
    cfg = BacktestConfig(
        target_ann_vol=args.target_vol,
        cost_bps=args.cost_bps,
        buffer_frac=args.buffer,
        max_leverage=args.max_leverage,
    )
    if args.signal == "ewmac":
        signal_fn = lambda px: ewmac_blend(px, smooth=args.smooth)
    else:
        signal_fn = lambda px: trend_signal(px, lookbacks=lookbacks, smooth=args.smooth)

    result = run(prices, signal_fn, cfg)
    print(result.report())

    dsr = metrics.deflated_sharpe_hint(
        result.stats["sharpe"], args.trials, result.stats["n_days"]
    )
    print(f"\nvariants tried to date: {args.trials}  "
          f"-> expected max Sharpe from pure noise: {dsr:.2f}")
    print("Raise --trials for EVERY parameter set you test, cumulatively. "
          "A Sharpe that does not clear this bar is not an edge.")
    cost_drag = result.stats["ann_turnover"] * args.cost_bps / 1e4
    print(f"cost drag at {args.cost_bps:.0f}bps: {cost_drag:.2%}/yr "
          f"({cost_drag / max(result.stats['cagr'], 1e-9):.0%} of gross CAGR)")

    outdir = Path(args.out)
    outdir.mkdir(exist_ok=True)
    pd.DataFrame({
        "net_return": result.net_returns,
        "equity": result.equity,
        "turnover": result.turnover,
    }).to_csv(outdir / f"{args.universe}_equity.csv")
    result.positions.to_csv(outdir / f"{args.universe}_positions.csv")
    print(f"\nwrote {outdir}/{args.universe}_equity.csv and _positions.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
