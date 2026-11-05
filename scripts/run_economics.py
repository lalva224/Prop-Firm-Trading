#!/usr/bin/env python3
"""What does this actually pay - own capital vs prop capital, gross vs net.

Three numbers that are easy to conflate and mean very different things:

  OWN CAPITAL    the strategy's unconstrained return on money you already have.
                 No fee, no split, no knockout - but you must own the capital
                 and you eat the full drawdown.
  GROSS IN ACCT  what the strategy earns inside a funded account. Lower than
                 the unconstrained return, because the knockout truncates the
                 bad runs: a 12% drawdown that would have recovered instead
                 ends the account.
  NET TO US      the profit split applied to GROSS, minus every challenge fee
                 paid along the way, including the ones that failed.

The spread between the first and the last is the real cost of the prop route,
and it is paid in two currencies: the firm's 20%, and the knockout.

Both an in-sample and a haircut basis are reported. The haircut rescales the
mean return so realised Sharpe equals the walk-forward figure (0.63) instead of
the in-sample one (~0.82), holding volatility at target. Parameters were chosen
on this data; the haircut is the honest basis for a forward projection.

Usage:
    scripts/run_economics.py
    scripts/run_economics.py --account 100000 --horizon-years 3
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from tcs.backtest import BacktestConfig, run
from tcs.data import load_ohlc
from tcs.ftmo import FTMORules, brownian_adverse, daily_paths, run_funded, run_phase
from tcs.metrics import sharpe
from tcs.signals import ewmac_blend
from tcs.universe import UNIVERSES

TRADING_DAYS = 252
# docs/04 measured held-out Sharpe 0.63 against ~0.83 in sample on the ETF
# universe. That ratio - not the absolute 0.63 - is what transfers to another
# universe, so the haircut is applied proportionally.
DECAY = 0.63 / 0.83


def chain(
    rets: np.ndarray,
    adverse: np.ndarray,
    rules: FTMORules,
    horizon: int,
) -> dict:
    """Buy a challenge, trade it, rebuy on breach, until the horizon.

    A failed challenge still costs a fee, and a phase with no time limit can
    consume months without ever reaching the target. Both are charged here;
    quoting the payout of a surviving account alone is how prop-firm maths
    usually goes wrong.
    """
    i = 0
    paid = 0.0          # to us, after split
    fees = 0.0
    n_challenges = 0
    funded_days = 0

    while i < horizon:
        fees += rules.fee_frac
        n_challenges += 1
        passed = True
        for target in rules.phase_targets:
            res = run_phase(rets[i:horizon], adverse[i:horizon], target, rules)
            i += res.days
            if res.outcome != "pass":
                passed = False
                break
        if i >= horizon:
            break
        if not passed:
            continue
        pay, days = run_funded(rets[i:horizon], adverse[i:horizon], rules,
                               model_scaling=False)
        paid += pay
        funded_days += days
        i += days

    return dict(paid=paid, fees=fees, n_challenges=n_challenges,
                funded_days=funded_days)


def haircut(rets: pd.Series, ratio: float = DECAY) -> pd.Series:
    """Rescale the mean to `ratio` x the in-sample Sharpe; keep the shape.

    Only the drift is touched. Volatility, autocorrelation, fat tails and the
    timing of drawdowns are exactly the historical ones - which is the point,
    because those are what the drawdown rules bite on.
    """
    return rets - rets.mean() + rets.mean() * ratio


def strategy_paths(ohlc, vol, smooth=10, buffer_frac=0.30, cost_bps=3.0):
    cfg = BacktestConfig(target_ann_vol=vol, max_leverage=12.0,
                         cost_bps=cost_bps, buffer_frac=buffer_frac)
    pos = run(ohlc["close"], lambda p: ewmac_blend(p, smooth=smooth), cfg).positions
    ret, _ = daily_paths(pos.shift(1), ohlc)
    return ret


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default="ftmo", choices=list(UNIVERSES))
    p.add_argument("--account", type=float, default=100_000.0)
    p.add_argument("--horizon-years", type=float, default=3.0)
    p.add_argument("--step", type=int, default=5)
    p.add_argument("--vols", default="0.10,0.15,0.20")
    p.add_argument("--decay", type=float, default=DECAY,
                   help="in-sample -> out-of-sample Sharpe ratio")
    args = p.parse_args()
    warnings.filterwarnings("ignore")

    ohlc = load_ohlc(list(UNIVERSES[args.universe]), start="2005-01-01",
                     cache_key=f"{args.universe}_ohlc_2005")
    rules = FTMORules()
    horizon = int(args.horizon_years * TRADING_DAYS)
    yrs = args.horizon_years
    A = args.account

    print(f"FTMO 2-step rules, ${A:,.0f} account, {yrs:g}-year horizon, "
          f"fee {rules.fee_frac:.1%} of account (${rules.fee_frac * A:,.0f})")
    print(f"split {rules.profit_split:.0%}/{1 - rules.profit_split:.0%}, "
          f"{rules.max_loss:.0%} static max loss, {rules.max_daily_loss:.0%} daily, "
          f"payout every {rules.payout_every}d\n")

    hdr = (f"{'basis':>10}{'vol':>6}{'Sharpe':>8}{'own capital':>13}"
           f"{'gross in acct':>15}{'NET to us':>12}{'firm take':>11}"
           f"{'chals':>7}{'funded%':>9}")
    print(hdr)
    print("-" * len(hdr))

    for vol in [float(v) for v in args.vols.split(",")]:
        base = strategy_paths(ohlc, vol)
        for label, series in (("in-sample", base),
                              (f"x{args.decay:.2f}", haircut(base, args.decay))):
            r = series.to_numpy(float)
            a = brownian_adverse(series).to_numpy(float)
            sh = float(sharpe(series))

            # Own capital: unconstrained compounded return, same windows.
            own = np.array([
                (np.prod(1.0 + r[s:s + horizon]) ** (1.0 / yrs) - 1.0)
                for s in range(0, len(r) - horizon, args.step)
            ])

            out = [chain(r[s:], a[s:], rules, horizon)
                   for s in range(0, len(r) - horizon, args.step)]
            net = np.array([o["paid"] - o["fees"] for o in out]) * A / yrs
            gross = np.array([o["paid"] for o in out]) / rules.profit_split * A / yrs
            fees = np.array([o["fees"] for o in out]) * A / yrs
            take = gross * (1 - rules.profit_split) + fees
            funded = np.mean([o["funded_days"] for o in out]) / horizon

            print(f"{label:>10}{vol:>6.0%}{sh:>8.2f}{own.mean() * A:>13,.0f}"
                  f"{gross.mean():>15,.0f}{net.mean():>12,.0f}{take.mean():>11,.0f}"
                  f"{np.mean([o['n_challenges'] for o in out]):>7.1f}"
                  f"{funded:>9.0%}")
        print()

    print("own capital = unconstrained return on YOUR OWN money (no fee, no "
          "split, no knockout)\ngross in acct = strategy profit inside the "
          "funded account, after the knockout truncates\nNET to us = our share "
          "minus every fee, including failed challenges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
