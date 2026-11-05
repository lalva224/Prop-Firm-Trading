#!/usr/bin/env python3
"""Turn an MT5 broker export into a decision.

    python3 scripts/analyze_mt5.py --dir "<...>/MQL5/Files/tcs_history"
    python3 scripts/analyze_mt5.py --dir ... --backtest       # run the strategy on it
    python3 scripts/analyze_mt5.py --dir ... --backtest --ftmo # and the FTMO harness

Answers, in order:
  1. How much history did the broker actually give us?
  2. Does ANY bond or rate instrument exist? (the sleeve worth Sharpe 0.87)
  3. What do the instruments cost in spread?
  4. What is the minimum position size, and is it viable on this account?
  5. On broker data free of roll artefacts, does the strategy have an edge?
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from tcs import mt5_data

RATE_WORDS = ("bond", "bund", "gilt", "treasury", "note", "btp", "oat",
              "jgb", "rate", "schatz", "bobl", "ultra", "tnote", "tbond")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--min-bars", type=int, default=500)
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--ftmo", action="store_true")
    ap.add_argument("--vol", type=float, default=0.12)
    ap.add_argument("--equity", type=float, default=100_000.0)
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    print("\n=== 1. history coverage ===\n")
    print(mt5_data.coverage(a.dir))

    print("\n=== 2. instrument groups (does a BOND exist?) ===\n")
    cls = mt5_data.classify(a.dir)
    if cls.empty:
        print("no index file - run DumpHistory.mq5 first")
        return 1
    grp = cls.groupby("group").agg(n=("symbol", "size"),
                                   median_years=("years", "median")).sort_values("n", ascending=False)
    print(grp.round(1).to_string())

    blob = (cls["symbol"].fillna("") + " " + cls["group"].fillna("") + " "
            + cls["subgroup"].fillna("") + " " + cls["description"].fillna("")).str.lower()
    rates = cls[blob.str.contains("|".join(RATE_WORDS), regex=True)]
    if rates.empty:
        print("\n  *** NO bond or rate instrument found. ***")
        print("  The bond/credit sleeve measured Sharpe 0.87 and is the single")
        print("  best component in the system. Without it the ceiling is ~0.35.")
    else:
        print(f"\n  BOND/RATE INSTRUMENTS FOUND ({len(rates)}):")
        print(rates[["symbol", "group", "years", "description"]].to_string(index=False))
        print("\n  This materially raises the ceiling - re-run with --backtest.")

    print("\n=== 3. spreads and sizing ===\n")
    ohlc = mt5_data.load_ohlc(a.dir, min_bars=a.min_bars)
    px, spr = ohlc["close"], ohlc["spread_bps"]
    cost = spr.tail(252).median().sort_values()
    tbl = pd.DataFrame({
        "median_spread_bps": cost,
        "last_price": px.iloc[-1].reindex(cost.index),
    })
    print(f"{'symbol':<18}{'spread bps':>12}{'last price':>14}")
    print("-" * 46)
    for s, r in tbl.head(12).iterrows():
        print(f"{s:<18}{r['median_spread_bps']:>12.2f}{r['last_price']:>14.4f}")
    if len(tbl) > 12:
        print(f"  ... {len(tbl) - 12} more")
    print(f"\n  median spread across all: {cost.median():.2f} bps")
    print(f"  turnover is ~13.6x/yr, so spread drag ~{cost.median() * 13.6 / 1e4:.2%}/yr")

    if not a.backtest:
        print("\nrun with --backtest to measure the strategy on this data")
        return 0

    from tcs.backtest import BacktestConfig, run
    from tcs.signals import ewmac_blend
    from tcs import metrics

    print("\n=== 5. strategy on BROKER data (no roll artefacts) ===\n")
    fn = lambda p: ewmac_blend(p, smooth=10)
    cost_bps = float(max(cost.median(), 1.0))
    split = px.index[int(len(px) * 0.6)]
    print(f"using {cost_bps:.1f}bps costs, IS/OOS split at {split.date()}\n")
    print(f"{'window':<22}{'n':>4}{'Sharpe':>9}{'CAGR':>9}{'maxDD':>9}{'turn':>8}")
    print("-" * 61)
    for label, sl in (("full", px), ("in-sample", px.loc[:split]),
                      ("OUT-OF-SAMPLE", px.loc[split:])):
        s = run(sl, fn, BacktestConfig(target_ann_vol=a.vol, max_leverage=12.0,
                                       cost_bps=cost_bps)).stats
        print(f"{label:<22}{sl.shape[1]:>4}{s['sharpe']:>9.2f}{s['cagr']:>9.2%}"
              f"{s['max_dd']:>9.1%}{s['ann_turnover']:>7.1f}x")

    r = px.pct_change().dropna(how="all")
    c = r.corr().fillna(0).to_numpy()
    ev = np.linalg.eigvalsh(c); ev = ev[ev > 1e-9]
    print(f"\neffective independent bets: {(ev.sum()**2)/(ev**2).sum():.1f} "
          f"of {px.shape[1]} instruments")
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    se = np.sqrt((1 + 0.5**2 / 2) / max(yrs, 0.1))
    print(f"Sharpe standard error over {yrs:.1f}y: +/-{se:.2f} "
          f"- treat differences smaller than that as noise")

    if a.ftmo:
        from tcs.ftmo import FTMORules, brownian_adverse, daily_paths, evaluate
        print("\n=== 6. FTMO challenge EV on broker data ===\n")
        rules = FTMORules()
        pos = run(px, fn, BacktestConfig(target_ann_vol=a.vol, max_leverage=12.0,
                                         cost_bps=cost_bps)).positions
        ret, worst = daily_paths(pos.shift(1), ohlc)
        fees = {100_000: 619.0 / 100_000, 50_000: 399.0 / 50_000}
        print(f"{'model':<12}{'pass':>8}{'brch day':>10}{'payout':>9}"
              f"{'EV/fee $100k':>14}{'EV/fee $50k':>13}")
        print("-" * 66)
        evs = []
        for m, adv, intr in (("close", worst, False),
                             ("brownian", brownian_adverse(ret), True),
                             ("worst", worst, True)):
            st = evaluate(ret, adv, rules, use_intraday=intr)
            if not st:
                continue
            ratios = [(st["mean_payout"] - f) / f for f in fees.values()]
            evs.append(ratios[0])
            print(f"{m:<12}{st['p_pass']:>8.1%}{st['p_breach_daily']:>10.1%}"
                  f"{st['mean_payout']:>9.2%}{ratios[0]:>13.2f}x{ratios[1]:>12.2f}x")
        if evs:
            print(f"\nmodel spread {max(evs) - min(evs):.1f}x -> "
                  f"{'ROBUST' if max(evs) - min(evs) < 1.5 else 'FRAGILE'}")
            print(f"pre-registered gate: worst-model EV/fee >= 2.0x  ->  "
                  f"{'PASS' if min(evs) >= 2.0 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
