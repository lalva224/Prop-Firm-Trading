"""Faithful FTMO challenge and funded-account simulator.

Rules as published for 2026 (verify before paying any fee):

  Phase 1 (Challenge):    +10% profit target
  Phase 2 (Verification): +5% profit target
  Max daily loss:         5% OF THE INITIAL BALANCE, reset at midnight server
                          time, measured from that day's starting balance
  Max overall loss:       10% OF THE INITIAL BALANCE - static, the floor never
                          moves up
  Minimum trading days:   4 per phase
  Time limit:             none (the old 30/60-day caps were removed)
  Profit split:           80/20, rising to 90/10 on the Scaling Plan
  First payout:           14 days after the first trade, then on demand

THREE DETAILS THAT MOST SIMULATIONS GET WRONG, and which this one handles:

1. Both loss limits are percentages of the INITIAL balance, not of current
   equity. At +8% profit your daily allowance is still 5% of the original
   balance, not of the grown one. This makes the rule *tighter* as you profit
   in absolute terms relative to your equity.
2. Both limits are evaluated on EQUITY INCLUDING FLOATING P&L. A position down
   5% intraday breaches even if it closes flat. Simulating on daily closes
   alone understates breach probability - sometimes badly.
3. The daily limit is measured from the START-OF-DAY balance, so a day that
   opens after a gain has a higher absolute floor than the overall limit.

For (2) this module takes an `adverse` series - the within-day worst-case
equity excursion built from each instrument's own High/Low against the position
it was holding. Summing adverse excursions across instruments assumes they all
hit their worst simultaneously, which is pessimistic; close-only is optimistic.
Both bounds are reported, and the truth is between them. Plan with the
pessimistic one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FTMORules:
    name: str = "FTMO Challenge 2-Step"
    phase_targets: tuple[float, ...] = (0.10, 0.05)
    max_daily_loss: float = 0.05     # of INITIAL balance
    max_loss: float = 0.10           # of INITIAL balance, static floor
    min_trading_days: int = 4
    max_days: int | None = None
    profit_split: float = 0.80
    scaled_split: float = 0.90
    scaling_threshold: float = 0.10  # return per cycle to qualify for scaling
    first_payout_days: int = 14
    payout_every: int = 14
    fee_frac: float = 0.012


# The FREE TRIAL is not the challenge: one phase, half the target, half the
# minimum days. Passing it therefore says almost nothing about passing the real
# thing - which is exactly why it should be used to validate EXECUTION rather
# than outcome.
FREE_TRIAL = FTMORules(
    name="FTMO Free Trial (2-Step variant)",
    phase_targets=(0.05,),
    max_daily_loss=0.05,
    max_loss=0.10,
    min_trading_days=2,
    max_days=14,
    profit_split=0.0,      # a trial pays nothing; it cannot be withdrawn from
    fee_frac=0.0,
)


@dataclass
class PhaseResult:
    outcome: str      # "pass" | "breach_daily" | "breach_total" | "timeout"
    days: int
    peak_profit: float


def daily_paths(
    positions: pd.DataFrame,
    ohlc: dict[str, pd.DataFrame],
) -> tuple[pd.Series, pd.Series]:
    """Portfolio close-to-close return and within-day adverse excursion.

    `positions` are weights decided at t and (per the backtest engine's
    convention) already lagged, so row t is the weight held into day t.

    Adverse excursion: for a long, the day's Low is what hurts; for a short,
    the High. Returned as a negative fraction of equity.
    """
    close, high, low = ohlc["close"], ohlc["high"], ohlc["low"]
    held = positions.reindex_like(close).fillna(0.0)
    prev = close.shift(1)

    ret = (close / prev - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    up = (high / prev - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    dn = (low / prev - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    adverse_leg = held * np.where(held > 0, dn, up)
    port_ret = (held * ret).sum(axis=1)
    # Only the legs that are actually losing contribute to the worst case.
    port_adverse = adverse_leg.clip(upper=0.0).sum(axis=1)
    # The worst intraday point cannot be better than the close.
    port_adverse = np.minimum(port_adverse, port_ret)
    return port_ret, pd.Series(port_adverse, index=port_ret.index)


def brownian_adverse(
    port_ret: pd.Series, halflife: int = 32, k: float = 0.8
) -> pd.Series:
    """Realistic within-day adverse excursion, as a Brownian-bridge estimate.

    Summing every instrument's worst intraday move (see daily_paths) assumes
    they all bottom simultaneously - a hard upper bound on the damage. Treating
    the PORTFOLIO as a single diffusion is the realistic middle: for a Brownian
    path with daily sigma, the expected within-day minimum sits about 0.8 sigma
    below the start. This automatically accounts for diversification, because
    the portfolio's own sigma is already net of correlations.

    The three models bracket the truth:
      close-only          optimistic  - understates breaches
      brownian_adverse    realistic   - use this one
      daily_paths sum     pessimistic - overstates breaches
    """
    sigma = port_ret.ewm(halflife=halflife, min_periods=halflife).std()
    return pd.concat([port_ret, -k * sigma], axis=1).min(axis=1).fillna(0.0)


def run_phase(
    rets: np.ndarray,
    adverse: np.ndarray,
    target: float,
    rules: FTMORules,
    use_intraday: bool = True,
) -> PhaseResult:
    """One phase, equity-based, limits as fractions of the initial balance."""
    limit = rules.max_days or len(rets)
    floor = 1.0 - rules.max_loss          # static, never moves
    equity = 1.0
    peak = 0.0

    for i in range(min(limit, len(rets))):
        start_of_day = equity
        low = equity * (1.0 + (adverse[i] if use_intraday else min(rets[i], 0.0)))
        equity = equity * (1.0 + rets[i])

        # Rule 2: breach is checked on the intraday LOW, not the close.
        if low <= floor:
            return PhaseResult("breach_total", i + 1, peak)
        # Rule 3: daily allowance is 5% of INITIAL balance, from today's open.
        if low <= start_of_day - rules.max_daily_loss:
            return PhaseResult("breach_daily", i + 1, peak)

        peak = max(peak, equity - 1.0)
        if equity - 1.0 >= target and (i + 1) >= rules.min_trading_days:
            return PhaseResult("pass", i + 1, peak)

    return PhaseResult("timeout", min(limit, len(rets)), peak)


def run_funded(
    rets: np.ndarray,
    adverse: np.ndarray,
    rules: FTMORules,
    use_intraday: bool = True,
    model_scaling: bool = True,
) -> tuple[float, int]:
    """Cumulative payout to the trader and days survived.

    First payout at `first_payout_days` after the start, then every
    `payout_every` days. Each payout resets equity to the initial balance; the
    static floor is unchanged, so paying yourself never increases your risk.
    """
    floor = 1.0 - rules.max_loss
    equity = 1.0
    paid = 0.0
    split = rules.profit_split
    good_cycles = 0

    for i in range(len(rets)):
        start_of_day = equity
        low = equity * (1.0 + (adverse[i] if use_intraday else min(rets[i], 0.0)))
        equity = equity * (1.0 + rets[i])

        if low <= floor or low <= start_of_day - rules.max_daily_loss:
            return paid, i + 1

        due = (i + 1) >= rules.first_payout_days and (
            (i + 1 - rules.first_payout_days) % rules.payout_every == 0
        )
        if due and equity > 1.0:
            gross = equity - 1.0
            paid += split * gross
            if model_scaling:
                good_cycles = good_cycles + 1 if gross >= rules.scaling_threshold else 0
                if good_cycles >= 3:
                    split = rules.scaled_split
            equity = 1.0

    return paid, len(rets)


def evaluate(
    returns: pd.Series,
    adverse: pd.Series,
    rules: FTMORules | None = None,
    funded_days: int = 504,
    step: int = 5,
    use_intraday: bool = True,
) -> dict:
    """Walk every historical window through both phases, then the funded account."""
    rules = rules or FTMORules()
    r = returns.to_numpy(dtype=float)
    a = adverse.reindex_like(returns).to_numpy(dtype=float)
    need = 252 + funded_days
    if len(r) < need + step:
        return {}

    outcomes, payouts, survived = [], [], []
    for s in range(0, len(r) - need, step):
        cursor, ok = s, True
        for target in rules.phase_targets:
            res = run_phase(r[cursor:cursor + 252], a[cursor:cursor + 252],
                            target, rules, use_intraday)
            cursor += res.days
            if res.outcome != "pass":
                outcomes.append(res.outcome)
                ok = False
                break
        if not ok:
            payouts.append(0.0)
            continue
        outcomes.append("pass")
        pay, days = run_funded(r[cursor:cursor + funded_days],
                               a[cursor:cursor + funded_days], rules, use_intraday)
        payouts.append(pay)
        survived.append(days)

    arr = np.array(payouts)
    n = len(outcomes)
    return {
        "n_windows": n,
        "p_pass": outcomes.count("pass") / n,
        "p_breach_daily": outcomes.count("breach_daily") / n,
        "p_breach_total": outcomes.count("breach_total") / n,
        "p_timeout": outcomes.count("timeout") / n,
        "mean_payout": float(arr.mean()),
        "payout_if_passed": float(arr[arr > 0].mean()) if (arr > 0).any() else 0.0,
        "ev": float(arr.mean()) - rules.fee_frac,
        "ev_per_fee": (float(arr.mean()) - rules.fee_frac) / rules.fee_frac,
        "mean_funded_days": float(np.mean(survived)) if survived else 0.0,
    }
