#!/usr/bin/env python3
"""The5ers Hyper Growth simulator - account lifetime and payouts.

Hyper Growth is structurally unlike FTMO and needs its own simulator, because
three of its rules have no FTMO analogue:

  1. The 3% daily limit is a PAUSE, not a breach. Trades are closed and the
     account is disabled until the next server day, then it resumes. So a day
     cannot cost more than 3%, and death requires the equity to already be
     within 3% of the floor.
  2. The 6% stop-out is measured on EQUITY (floating P&L included) but is a
     FIXED level computed from the starting balance OF EACH LEVEL - it does not
     trail the equity high. Official wording: "the stopout level is a fixed
     value of loss allowance measured from the starting balance account of each
     level. As much the trader profit in the account, so his/hers loss
     allowance increases."
  3. Withdrawing does not reset SCALING progress - progress toward the +10%
     milestone is cumulative profit, tracked independently of what has already
     been paid out. But it does reset the loss BUFFER, because the stop-out is
     fixed at the level's starting balance: profit left in the account is the
     only thing that widens the gap to the floor. So the withdraw-vs-accumulate
     trade-off survives; it just moves from scaling to survival. `--keep` is
     the buffer, in level units, retained before any payout.

Account sizes purchasable are $5k / $10k / $20k, capped at $40k of evaluation
capital per trader - there is no $100k Hyper Growth account. The ladder doubles
on every +10%: 20k -> 40k -> 80k -> ... up to a $4M ceiling.

Usage:
    scripts/run_5ers.py                      # lifetime + payouts by vol
    scripts/run_5ers.py --pessimistic        # no credit for the pause fill
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
from tcs.ftmo import brownian_adverse, daily_paths
from tcs.signals import ewmac_blend
from tcs.universe import UNIVERSES

TRADING_DAYS = 252


class HyperGrowth:
    """Published Hyper Growth rules, 2026."""

    target = 0.10          # of the level's starting balance
    stop_out = 0.06        # of the level's starting balance, fixed, on equity
    daily_pause = 0.03     # of base = max(prev close balance, equity)
    payout_every = 14      # calendar -> approximated as trading days
    ceiling_mult = 200.0   # $20k -> $4M
    # 50% at level 1, 75% from level 2, "up to 100%" at advanced milestones.
    splits = (0.50, 0.75)

    def split(self, level: int) -> float:
        return self.splits[min(level - 1, len(self.splits) - 1)]


def simulate(
    rets: np.ndarray,
    adverse: np.ndarray,
    rules: HyperGrowth,
    pause_fills: bool = True,
    keep: float = 0.0,
) -> dict:
    """One account, from level 1 until stop-out or end of series.

    Equity is carried in units of the CURRENT level's starting balance, and
    `scale` tracks the real account size so payouts can be summed in dollars.
    """
    eq = 1.0               # equity / level starting balance
    scale = 1.0            # level starting balance / initial balance
    level = 1
    progress = 0.0         # cumulative profit this level, in level units
    paid = 0.0             # payouts in units of the INITIAL balance
    paused_days = 0
    doublings = 0

    floor = 1.0 - rules.stop_out
    i = 0
    n = len(rets)
    while i < n:
        # "3% of the previous day's closing balance or equity, whichever is
        # higher" - an allowance spent from TODAY's opening equity.
        base = max(eq, 1.0)
        start_of_day = eq
        pause_at = eq - rules.daily_pause * base
        low = eq * (1.0 + adverse[i])

        # The stop-out is checked on the intraday low, floating P&L included.
        # It can only be reached from above if the day blows through the pause
        # level in one move (a gap); otherwise the pause closes trades first.
        if low <= floor:
            return dict(days=i + 1, dead=True, paid=paid, level=level,
                        doublings=doublings, paused=paused_days)

        if low <= pause_at:
            # Trades closed at the pause level (optimistic) or at the day's
            # true adverse low (pessimistic - a gap through the level).
            eq = pause_at if pause_fills else eq * (1.0 + adverse[i])
            progress += eq - start_of_day
            paused_days += 1
            if eq <= floor:
                return dict(days=i + 1, dead=True, paid=paid, level=level,
                            doublings=doublings, paused=paused_days)
            i += 1
            continue

        prev = eq
        eq *= 1.0 + rets[i]
        progress += eq - prev

        # Milestone: cumulative profit of +10%, regardless of what has already
        # been withdrawn. The account doubles and the floor moves with it.
        if progress >= rules.target:
            paid += rules.split(level) * max(0.0, eq - 1.0) * scale
            if scale * 2.0 <= rules.ceiling_mult:
                scale *= 2.0
                doublings += 1
            level += 1
            eq = 1.0
            progress = 0.0
            i += 1
            continue

        # Bi-weekly payout: realise profit above the level balance. Does not
        # touch `progress`, so it costs nothing in scaling terms.
        if (i + 1) % rules.payout_every == 0 and eq > 1.0 + keep:
            take = eq - 1.0 - keep
            paid += rules.split(level) * take * scale
            eq -= take

        i += 1

    return dict(days=n, dead=False, paid=paid, level=level,
                doublings=doublings, paused=paused_days)


def chain(rets, adverse, rules, horizon, fee_frac, pause_fills=True, keep=0.0):
    """Rebuy on every stop-out and keep trading to the horizon.

    The per-account payout is not the decision number: an account that dies in
    8 months costs another fee. Net = payouts - fees over a fixed 10 years.
    """
    i = 0
    paid = 0.0
    fees = fee_frac
    accounts = 1
    lives = []
    while i < horizon:
        o = simulate(rets[i:horizon], adverse[i:horizon], rules,
                     pause_fills=pause_fills, keep=keep)
        paid += o["paid"]
        lives.append(o["days"])
        i += o["days"]
        if o["dead"] and i < horizon:
            fees += fee_frac
            accounts += 1
    return dict(paid=paid, fees=fees, accounts=accounts,
                mean_life=float(np.mean(lives)))


def km_median(times: np.ndarray, dead: np.ndarray) -> float:
    """Kaplan-Meier median survival. nan if the curve never reaches 0.5."""
    order = np.argsort(times)
    t, d = times[order], dead[order]
    n_at_risk = len(t)
    s = 1.0
    for i in range(len(t)):
        if d[i]:
            s *= 1.0 - 1.0 / n_at_risk
            if s <= 0.5:
                return float(t[i])
        n_at_risk -= 1
    return float("nan")


def strategy_paths(ohlc, vol, smooth=10, buffer_frac=0.30, cost_bps=3.0):
    cfg = BacktestConfig(target_ann_vol=vol, max_leverage=12.0,
                         cost_bps=cost_bps, buffer_frac=buffer_frac)
    pos = run(ohlc["close"], lambda p: ewmac_blend(p, smooth=smooth), cfg).positions
    ret, _ = daily_paths(pos.shift(1), ohlc)
    return ret, brownian_adverse(ret)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default="ftmo", choices=list(UNIVERSES))
    p.add_argument("--start", default="2005-01-01")
    p.add_argument("--account", type=float, default=20_000.0)
    p.add_argument("--fee", type=float, default=850.0)
    p.add_argument("--step", type=int, default=5)
    p.add_argument("--horizon-years", type=float, default=10.0)
    p.add_argument("--pessimistic", action="store_true",
                   help="pause fills at the day's adverse low, not the level")
    p.add_argument("--vols", default="0.03,0.04,0.06,0.08,0.10,0.15")
    p.add_argument("--keeps", default="0.0,0.02,0.04,0.99",
                   help="buffer left in the account before payout; 0.99 = never withdraw")
    args = p.parse_args()
    warnings.filterwarnings("ignore")

    ohlc = load_ohlc(list(UNIVERSES[args.universe]), start=args.start,
                     cache_key=f"{args.universe}_ohlc_{args.start[:4]}")
    rules = HyperGrowth()
    horizon = int(args.horizon_years * TRADING_DAYS)
    fee_frac = args.fee / args.account

    print(f"The5ers Hyper Growth - ${args.account:,.0f} account, fee ${args.fee:,.0f}")
    print(f"universe={args.universe}  {ohlc['close'].index[0].date()} -> "
          f"{ohlc['close'].index[-1].date()}  horizon={args.horizon_years:g}y  "
          f"pause_fill={'adverse low' if args.pessimistic else 'at level'}")
    print(f"rules: +{rules.target:.0%} doubles, -{rules.stop_out:.0%} equity stop-out "
          f"(fixed per level), -{rules.daily_pause:.0%} daily PAUSE, "
          f"split {rules.splits[0]:.0%}->{rules.splits[1]:.0%}, ceiling $4M")
    print()
    hdr = (f"{'vol':>5}{'keep':>7}{'median life':>13}{'dead 1y':>9}{'dead 3y':>9}"
           f"{'dead 5y':>9}{'doublings':>11}{'accts/10y':>11}"
           f"{'gross 10y':>11}{'fees 10y':>10}{'NET 10y':>10}")
    print(hdr)
    print("-" * len(hdr))

    keeps = [float(k) for k in args.keeps.split(",")]
    for vol in [float(v) for v in args.vols.split(",")]:
        ret, adv = strategy_paths(ohlc, vol)
        r, a = ret.to_numpy(float), adv.to_numpy(float)
        starts = range(0, len(r) - TRADING_DAYS, args.step)
        for keep in keeps:
            out = [simulate(r[s:s + horizon], a[s:s + horizon], rules,
                            pause_fills=not args.pessimistic, keep=keep)
                   for s in starts]
            times = np.array([o["days"] for o in out], float)
            dead = np.array([o["dead"] for o in out])
            paid = np.array([o["paid"] for o in out]) * args.account
            yrs = times / TRADING_DAYS

            med = km_median(times, dead) / TRADING_DAYS
            d1, d3, d5 = (np.mean(dead & (times <= k * TRADING_DAYS)) for k in (1, 3, 5))
            ch = [chain(r[s:], a[s:], rules, horizon, fee_frac,
                        pause_fills=not args.pessimistic, keep=keep)
                  for s in starts if len(r) - s >= horizon]
            gross = np.mean([c["paid"] for c in ch]) * args.account
            cost = np.mean([c["fees"] for c in ch]) * args.account
            naccts = np.mean([c["accounts"] for c in ch])

            lab = "never" if keep > 0.5 else f"{keep:.0%}"
            print(f"{vol:>5.0%}{lab:>7}"
                  f"{(f'{med:.1f}y' if med == med else '>10y'):>13}"
                  f"{d1:>9.0%}{d3:>9.0%}{d5:>9.0%}"
                  f"{np.mean([o['doublings'] for o in out]):>11.2f}"
                  f"{naccts:>11.1f}{gross:>11,.0f}{cost:>10,.0f}"
                  f"{gross - cost:>10,.0f}")
        print()

    print(f"\nwindows per row: {len(out)} overlapping starts, step {args.step}d "
          f"(~{(len(r) / TRADING_DAYS):.0f} independent years)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
