"""Pre-trade risk guard.

This is the load-bearing safety component. It sits between the strategy's
desired positions and any order reaching a broker, and it has one job: make it
arithmetically difficult to breach a prop-firm loss limit.

WHY A GUARD AND NOT JUST THE FIRM'S ENGINE
FTMO's own limits are a backstop, not a control. By the time FTMO's 5% daily
limit triggers, the account has already failed - the rule is detection, not
prevention. Prevention has to happen before the order is submitted, which means
re-implementing the limits here and never letting the firm's engine fire.

THE TWO FLOORS, AND WHICH ONE BINDS
Both are fractions of the INITIAL balance and never grow with the account:

  total floor  = initial * (1 - max_total_loss)          <- fixed forever
  daily floor  = day_start_balance - initial * max_daily  <- resets each day

The binding floor is the HIGHER of the two, and it is usually the daily one.
Headroom is the distance from current EQUITY (including floating P&L) to that
binding floor.

SIZING, NOT JUST VETOING
A veto-only guard is useless: it blocks the order after the book is already too
big. Instead the guard asks "how large a one-day adverse move can this book
absorb before breaching?" and scales the target book down until its estimated
tail excursion fits inside the headroom with margin. The book therefore
de-risks automatically as it approaches a limit - which is exactly the
behaviour a human fails to execute under stress.

TAIL SIZING
`brownian_adverse` in ftmo.py uses 0.8 sigma, the EXPECTED within-day minimum.
That is the right number for estimating average damage and the wrong number for
a risk limit. Here we use `tail_sigmas` (default 3.0) because the guard must
survive a bad day, not a typical one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class Verdict(str, Enum):
    APPROVED = "approved"
    SCALED = "scaled"
    FLATTEN = "flatten"
    REFUSED = "refused"


@dataclass(frozen=True)
class AccountState:
    """Live account snapshot. `equity` must include floating P&L."""
    equity: float
    balance: float
    day_start_balance: float
    open_positions: dict[str, float] = field(default_factory=dict)
    age_seconds: float = 0.0
    connected: bool = True


@dataclass(frozen=True)
class GuardLimits:
    initial_balance: float
    max_daily_loss: float = 0.05      # fraction of INITIAL balance
    max_total_loss: float = 0.10      # fraction of INITIAL balance
    tail_sigmas: float = 3.0          # adverse move the book must survive
    safety_margin: float = 0.50       # use at most this share of headroom
    max_gross: float = 4.0            # hard cap on sum of |weights|
    hard_stop_headroom: float = 0.15  # below this share of the daily allowance, flatten
    max_state_age_s: float = 900.0    # refuse to trade on stale data


@dataclass
class GuardDecision:
    verdict: Verdict
    scale: float
    target_weights: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    # diagnostics, all in account currency unless noted
    total_floor: float = 0.0
    daily_floor: float = 0.0
    binding_floor: float = 0.0
    headroom: float = 0.0
    headroom_frac: float = 0.0
    budget_frac: float = 0.0
    est_tail_excursion: float = 0.0

    @property
    def tradeable(self) -> bool:
        return self.verdict in (Verdict.APPROVED, Verdict.SCALED, Verdict.FLATTEN)

    def report(self) -> str:
        lines = [
            f"verdict        {self.verdict.value.upper()}  (scale {self.scale:.3f})",
            f"total floor    {self.total_floor:,.2f}",
            f"daily floor    {self.daily_floor:,.2f}",
            f"binding floor  {self.binding_floor:,.2f}",
            f"headroom       {self.headroom:,.2f}  ({self.headroom_frac:.2%} of equity)",
            f"risk budget    {self.budget_frac:.2%} of equity",
            f"est tail move  {self.est_tail_excursion:.2%} of equity",
        ]
        if self.reasons:
            lines.append("reasons:")
            lines += [f"  - {r}" for r in self.reasons]
        return "\n".join(lines)


def portfolio_tail_excursion(
    weights: dict[str, float],
    cov: pd.DataFrame,
    tail_sigmas: float,
) -> float:
    """Estimated adverse one-day move of the book, as a fraction of equity.

    cov is a DAILY return covariance matrix. Using the full covariance rather
    than summing per-instrument risk is the point: a diversified book has a
    smaller tail than the sum of its parts, and charging it the sum would
    de-risk a well-built portfolio for no reason.
    """
    if not weights or cov.empty:
        return 0.0
    syms = [s for s in weights if s in cov.index]
    if not syms:
        return 0.0
    w = np.array([weights[s] for s in syms], dtype=float)
    c = cov.loc[syms, syms].to_numpy(dtype=float)
    var = float(w @ c @ w)
    return tail_sigmas * float(np.sqrt(max(var, 0.0)))


def evaluate(
    target_weights: dict[str, float],
    state: AccountState,
    limits: GuardLimits,
    cov: pd.DataFrame,
) -> GuardDecision:
    """Decide what, if anything, may be sent to the broker."""
    reasons: list[str] = []

    total_floor = limits.initial_balance * (1.0 - limits.max_total_loss)
    daily_allowance = limits.initial_balance * limits.max_daily_loss
    daily_floor = state.day_start_balance - daily_allowance
    binding_floor = max(total_floor, daily_floor)
    headroom = state.equity - binding_floor
    headroom_frac = headroom / state.equity if state.equity > 0 else 0.0

    def decision(v: Verdict, scale: float, weights: dict[str, float], tail: float):
        return GuardDecision(
            verdict=v, scale=scale, target_weights=weights, reasons=reasons,
            total_floor=total_floor, daily_floor=daily_floor,
            binding_floor=binding_floor, headroom=headroom,
            headroom_frac=headroom_frac,
            budget_frac=max(headroom_frac, 0.0) * limits.safety_margin,
            est_tail_excursion=tail,
        )

    # --- hard refusals: never trade blind ---
    if not state.connected:
        reasons.append("broker not connected")
        return decision(Verdict.REFUSED, 0.0, {}, 0.0)
    if state.age_seconds > limits.max_state_age_s:
        reasons.append(f"account state is {state.age_seconds:.0f}s old "
                       f"(limit {limits.max_state_age_s:.0f}s)")
        return decision(Verdict.REFUSED, 0.0, {}, 0.0)
    if state.equity <= 0:
        reasons.append("non-positive equity")
        return decision(Verdict.REFUSED, 0.0, {}, 0.0)

    # --- already breached: flatten, do not open anything ---
    if headroom <= 0:
        reasons.append(f"equity {state.equity:,.2f} at or below binding floor "
                       f"{binding_floor:,.2f} - flatten everything")
        return decision(Verdict.FLATTEN, 0.0, {}, 0.0)

    # --- near the floor: flatten before the firm's engine fires ---
    if headroom < limits.hard_stop_headroom * daily_allowance:
        reasons.append(
            f"headroom {headroom:,.2f} is under {limits.hard_stop_headroom:.0%} "
            f"of the daily allowance {daily_allowance:,.2f} - flatten"
        )
        return decision(Verdict.FLATTEN, 0.0, {}, 0.0)

    # --- size the book to fit the risk budget ---
    budget_frac = headroom_frac * limits.safety_margin
    tail = portfolio_tail_excursion(target_weights, cov, limits.tail_sigmas)

    scale = 1.0
    if tail > 0 and tail > budget_frac:
        scale = budget_frac / tail
        reasons.append(
            f"tail estimate {tail:.2%} exceeds budget {budget_frac:.2%} "
            f"- scaling book by {scale:.3f}"
        )

    gross = sum(abs(w) for w in target_weights.values()) * scale
    if gross > limits.max_gross:
        extra = limits.max_gross / gross
        scale *= extra
        reasons.append(f"gross {gross:.2f} exceeds cap {limits.max_gross:.2f} "
                       f"- scaling by a further {extra:.3f}")

    scaled = {s: w * scale for s, w in target_weights.items()}
    final_tail = portfolio_tail_excursion(scaled, cov, limits.tail_sigmas)
    verdict = Verdict.APPROVED if scale >= 0.999 else Verdict.SCALED
    return decision(verdict, scale, scaled, final_tail)
