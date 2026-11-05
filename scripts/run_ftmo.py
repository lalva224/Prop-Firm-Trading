#!/usr/bin/env python3
"""Test the strategy against FTMO's exact rules - challenge AND funded phase.

    # single configuration, all three intraday models
    python scripts/run_ftmo.py --vol 0.12

    # sweep volatility
    python scripts/run_ftmo.py --sweep-vol

    # does tuning actually help? walk-forward answer
    python scripts/run_ftmo.py --walk-forward

Everything runs on free daily data. No fee, no demo account, no live risk.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from tcs.backtest import BacktestConfig, run
from tcs.data import load_ohlc
from tcs.ftmo import FTMORules, brownian_adverse, daily_paths, evaluate
from tcs.signals import ewmac_blend
from tcs.tuning import walk_forward
from tcs.universe import UNIVERSES

MODELS = ("close", "brownian", "worst")


def strategy_paths(ohlc, vol, buffer_frac=0.30, smooth=10, cost_bps=3.0):
    """Returns (portfolio return, {model: adverse series})."""
    cfg = BacktestConfig(target_ann_vol=vol, max_leverage=12.0,
                         cost_bps=cost_bps, buffer_frac=buffer_frac)
    pos = run(ohlc["close"], lambda p: ewmac_blend(p, smooth=smooth), cfg).positions
    ret, worst = daily_paths(pos.shift(1), ohlc)
    return ret, {"close": worst, "brownian": brownian_adverse(ret), "worst": worst}


def report_one(ohlc, vol, rules, **kw):
    ret, adv = strategy_paths(ohlc, vol, **kw)
    rows = []
    for m in MODELS:
        s = evaluate(ret, adv[m], rules, use_intraday=(m != "close"))
        if s:
            rows.append({"model": m, **s})
    return pd.DataFrame(rows)


def print_report(df: pd.DataFrame, title: str) -> None:
    print(f"\n{title}")
    print(f"{'model':<12}{'pass':>8}{'brch day':>10}{'brch tot':>10}{'timeout':>9}"
          f"{'payout':>9}{'EV':>8}{'EV/fee':>8}{'funded d':>10}")
    print("-" * 84)
    for _, r in df.iterrows():
        print(f"{r['model']:<12}{r['p_pass']:>8.1%}{r['p_breach_daily']:>10.1%}"
              f"{r['p_breach_total']:>10.1%}{r['p_timeout']:>9.1%}"
              f"{r['mean_payout']:>9.2%}{r['ev']:>8.2%}{r['ev_per_fee']:>7.1f}x"
              f"{r['mean_funded_days']:>10.0f}")
    spread = df.ev_per_fee.max() - df.ev_per_fee.min()
    flag = "ROBUST - models agree" if spread < 1.5 else "FRAGILE - models disagree"
    print(f"  model spread: {spread:.1f}x  -> {flag}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default="etf", choices=list(UNIVERSES))
    p.add_argument("--start", default="2005-01-01")
    p.add_argument("--vol", type=float, default=0.12)
    p.add_argument("--buffer", type=float, default=0.30)
    p.add_argument("--smooth", type=int, default=10)
    p.add_argument("--cost-bps", type=float, default=3.0)
    p.add_argument("--sweep-vol", action="store_true")
    p.add_argument("--walk-forward", action="store_true")
    p.add_argument("--model", default="brownian", choices=MODELS,
                   help="intraday model used to score walk-forward folds")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    tickers = list(UNIVERSES[args.universe])
    ohlc = load_ohlc(tickers, start=args.start,
                     cache_key=f"{args.universe}_ohlc_{args.start[:4]}")
    rules = FTMORules()
    print(f"universe={args.universe}  {ohlc['close'].shape[1]} instruments  "
          f"{ohlc['close'].index[0].date()} -> {ohlc['close'].index[-1].date()}")
    print(f"rules: targets={rules.phase_targets} daily={rules.max_daily_loss:.0%} "
          f"total={rules.max_loss:.0%} static  min_days={rules.min_trading_days}  "
          f"split={rules.profit_split:.0%}")

    if args.sweep_vol:
        for v in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25):
            print_report(report_one(ohlc, v, rules, buffer_frac=args.buffer,
                                    smooth=args.smooth, cost_bps=args.cost_bps),
                         f"=== vol target {v:.0%} ===")
        return 0

    if args.walk_forward:
        # Score = FTMO EV on the slice, realistic intraday model.
        cache: dict[tuple, tuple] = {}

        def score(params, start, end):
            key = tuple(sorted(params.items()))
            if key not in cache:
                cache[key] = strategy_paths(
                    ohlc, params["vol"], params["buffer"], params["smooth"],
                    args.cost_bps,
                )
            ret, adv = cache[key]
            sl = slice(start, end)
            s = evaluate(ret.loc[sl], adv[args.model].loc[sl], rules,
                         use_intraday=(args.model != "close"),
                         funded_days=252, step=5)
            return s.get("ev", float("-inf")) if s else float("-inf")

        grid = [{"vol": v, "buffer": b, "smooth": sm}
                for v in (0.08, 0.10, 0.12, 0.15, 0.20)
                for b in (0.15, 0.30, 0.50)
                for sm in (5, 10, 20)]
        default = {"vol": 0.12, "buffer": 0.30, "smooth": 10}

        df, summary = walk_forward(score, grid, ohlc["close"].index, default,
                                   train_days=1512, test_days=1008, step_days=504)
        print("\n=== WALK-FORWARD: does tuning help? ===\n")
        if df.empty:
            print("not enough history for these fold sizes")
            return 1
        print(df.to_string(index=False))
        print("\nsummary")
        print("-" * 60)
        for k, v in summary.items():
            print(f"  {k:<22}{v if isinstance(v, (str, int)) else f'{v:.4f}'}")
        return 0

    print_report(report_one(ohlc, args.vol, rules, buffer_frac=args.buffer,
                            smooth=args.smooth, cost_bps=args.cost_bps),
                 f"=== vol target {args.vol:.0%} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
