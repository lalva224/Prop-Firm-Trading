"""Prop-firm challenge and funded-account simulator.

Runs a strategy's real return series against a firm's exact published rules,
over every historical window. This is the free test: no fee, no demo account,
full history, and it separates the two phases - which matter for different
reasons.

The key asymmetry the simulator exposes:

  CHALLENGE phase - you want to reach the target before breaching. Higher
      volatility helps, because the binding risk is TIMING OUT, not breaching.
  FUNDED phase   - you want maximum cumulative withdrawals before knockout.
      Lower volatility helps, and withdrawing early matters more than
      compounding, because a withdrawal converts a paper gain into a realised
      one that the knockout can no longer take back.

Rules as published in 2026; verify per firm before paying any fee. Drawdown
TYPE matters more than drawdown SIZE: a 4% trailing limit is harsher than a
10% static one, because trailing chases every new equity high.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class FirmRules:
    name: str
    phase_targets: tuple[float, ...]   # profit target per phase
    max_dd: float                      # max overall drawdown
    dd_type: str                       # "static" | "trailing"
    daily_loss: float | None           # max daily loss, None if no rule
    min_days: int                      # minimum trading days per phase
    max_days: int | None               # time limit per phase, None = unlimited
    profit_split: float
    consistency: float | None          # best day / total profit cap at payout
    fee_frac: float                    # challenge fee as fraction of account

    @property
    def n_phases(self) -> int:
        return len(self.phase_targets)


# Published 2026 rules. Static-drawdown + no-consistency firms are the ones
# compatible with a lumpy-profit strategy like trend following.
FIRMS: dict[str, FirmRules] = {
    "ftmo_2step": FirmRules(
        name="FTMO Challenge 2-Step",
        phase_targets=(0.10, 0.05), max_dd=0.10, dd_type="static",
        daily_loss=0.05, min_days=4, max_days=None,
        profit_split=0.85, consistency=None, fee_frac=0.012,
    ),
    "e8_pro": FirmRules(
        name="E8 Pro",
        phase_targets=(0.08,), max_dd=0.08, dd_type="static",
        daily_loss=0.04, min_days=0, max_days=None,
        profit_split=0.80, consistency=None, fee_frac=0.012,
    ),
    "the5ers_highstakes": FirmRules(
        name="The5ers High Stakes 2-Step",
        phase_targets=(0.08, 0.05), max_dd=0.10, dd_type="static",
        daily_loss=0.05, min_days=3, max_days=None,
        profit_split=0.80, consistency=None, fee_frac=0.012,
    ),
    "e8_one": FirmRules(
        name="E8 One (trailing)",
        phase_targets=(0.08,), max_dd=0.06, dd_type="trailing",
        daily_loss=0.04, min_days=0, max_days=None,
        profit_split=0.80, consistency=0.40, fee_frac=0.012,
    ),
    "futures_trailing": FirmRules(
        name="Futures firm (EOD trailing)",
        phase_targets=(0.06,), max_dd=0.04, dd_type="trailing",
        daily_loss=0.02, min_days=5, max_days=None,
        profit_split=0.90, consistency=0.50, fee_frac=0.008,
    ),
}


def _floor(equity: np.ndarray, dd: float, dd_type: str) -> np.ndarray:
    """Equity level at which the account is breached, per day."""
    if dd_type == "static":
        return np.full_like(equity, 1.0 - dd)
    peak = np.maximum(np.maximum.accumulate(equity), 1.0)
    return peak * (1.0 - dd)


def run_phase(
    rets: np.ndarray, target: float, rules: FirmRules
) -> tuple[str, int]:
    """Simulate one phase. Returns (outcome, days_used).

    outcome in {"pass", "breach", "timeout"}.
    """
    limit = rules.max_days or len(rets)
    r = rets[:limit]
    if len(r) == 0:
        return "timeout", 0

    eq = np.cumprod(1.0 + r)
    floor = _floor(eq, rules.max_dd, rules.dd_type)

    breach = eq <= floor
    if rules.daily_loss is not None:
        breach = breach | (r <= -rules.daily_loss)

    reached = (eq - 1.0 >= target) & (np.arange(len(r)) + 1 >= rules.min_days)

    i_breach = int(np.argmax(breach)) if breach.any() else len(r)
    i_pass = int(np.argmax(reached)) if reached.any() else len(r)

    if i_pass < i_breach:
        return "pass", i_pass + 1
    if i_breach < len(r):
        return "breach", i_breach + 1
    return "timeout", len(r)


def run_funded(
    rets: np.ndarray, rules: FirmRules, withdraw_every: int = 21
) -> float:
    """Cumulative payout to the trader, as a fraction of account size.

    Withdrawals are taken every `withdraw_every` days. Each withdrawal removes
    profit above the starting balance, which both realises the gain and - under
    static drawdown - leaves the floor untouched. This is why withdrawing early
    dominates compounding inside a funded account: the knockout cannot reclaim
    money already paid out.
    """
    eq = 1.0
    peak = 1.0
    paid = 0.0
    best_day = 0.0
    since_payout = 0.0

    for i, r in enumerate(rets):
        if rules.daily_loss is not None and r <= -rules.daily_loss:
            break
        eq *= 1.0 + r
        peak = max(peak, eq)
        floor = (1.0 - rules.max_dd) if rules.dd_type == "static" else peak * (1.0 - rules.max_dd)
        if eq <= floor:
            break

        day_profit = eq * r
        best_day = max(best_day, day_profit)
        since_payout += day_profit

        if (i + 1) % withdraw_every == 0 and eq > 1.0:
            gross = eq - 1.0
            # Consistency is a payout gate, not a breach: skip this payout if
            # one day dominates the profit since the last withdrawal.
            if rules.consistency is not None and since_payout > 0:
                if best_day / since_payout > rules.consistency:
                    continue
            paid += rules.profit_split * gross
            eq = 1.0
            if rules.dd_type == "trailing":
                peak = 1.0
            best_day = 0.0
            since_payout = 0.0

    return paid


def evaluate(
    returns: pd.Series,
    rules: FirmRules,
    funded_days: int = TRADING_DAYS * 2,
    step: int = 5,
    withdraw_every: int = 21,
) -> dict:
    """Walk every historical window: challenge phases, then the funded account."""
    v = returns.to_numpy(dtype=float)
    need = TRADING_DAYS + funded_days
    if len(v) < need + step:
        return {}

    outcomes: list[str] = []
    payouts: list[float] = []

    for s in range(0, len(v) - need, step):
        cursor = s
        ok = True
        for target in rules.phase_targets:
            res, used = run_phase(v[cursor:cursor + TRADING_DAYS], target, rules)
            cursor += used
            if res != "pass":
                outcomes.append(res)
                ok = False
                break
        if not ok:
            payouts.append(0.0)
            continue
        outcomes.append("pass")
        payouts.append(run_funded(v[cursor:cursor + funded_days], rules, withdraw_every))

    arr = np.array(payouts)
    n = len(outcomes)
    p_pass = outcomes.count("pass") / n
    return {
        "firm": rules.name,
        "n_windows": n,
        "p_pass": p_pass,
        "p_breach": outcomes.count("breach") / n,
        "p_timeout": outcomes.count("timeout") / n,
        "mean_payout": float(arr.mean()),
        "payout_if_passed": float(arr[arr > 0].mean()) if (arr > 0).any() else 0.0,
        "ev": float(arr.mean()) - rules.fee_frac,
        "ev_per_fee": (float(arr.mean()) - rules.fee_frac) / rules.fee_frac,
    }
