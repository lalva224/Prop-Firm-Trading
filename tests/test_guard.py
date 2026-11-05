"""Tests for the pre-trade risk guard.

The guard is the only thing standing between a strategy bug and a failed
funded account, so its edge cases are encoded here rather than discovered live.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.guard import (
    AccountState,
    GuardLimits,
    Verdict,
    evaluate,
    portfolio_tail_excursion,
)

SYMS = ["A", "B", "C", "D"]
LIM = GuardLimits(initial_balance=100_000.0)


def cov(daily_vol: float = 0.01, corr: float = 0.0, n: int = 4) -> pd.DataFrame:
    c = np.full((n, n), corr * daily_vol**2)
    np.fill_diagonal(c, daily_vol**2)
    return pd.DataFrame(c, index=SYMS[:n], columns=SYMS[:n])


def fresh(**kw) -> AccountState:
    base = dict(equity=100_000.0, balance=100_000.0, day_start_balance=100_000.0)
    base.update(kw)
    return AccountState(**base)


# --- hard refusals ---------------------------------------------------------

def test_refuses_when_disconnected():
    d = evaluate({"A": 0.1}, fresh(connected=False), LIM, cov())
    assert d.verdict is Verdict.REFUSED
    assert not d.target_weights


def test_refuses_on_stale_state():
    d = evaluate({"A": 0.1}, fresh(age_seconds=3600), LIM, cov())
    assert d.verdict is Verdict.REFUSED
    assert "old" in " ".join(d.reasons)


def test_refuses_on_non_positive_equity():
    d = evaluate({"A": 0.1}, fresh(equity=0.0), LIM, cov())
    assert d.verdict is Verdict.REFUSED


# --- the two floors --------------------------------------------------------

def test_daily_floor_binds_on_a_fresh_account():
    """Fresh account: daily floor (95k) is above the total floor (90k)."""
    d = evaluate({"A": 0.01}, fresh(), LIM, cov())
    assert d.daily_floor == pytest.approx(95_000.0)
    assert d.total_floor == pytest.approx(90_000.0)
    assert d.binding_floor == pytest.approx(95_000.0)


def test_total_floor_binds_after_a_losing_run():
    """Day opens at 92k: daily floor is 87k, below the fixed 90k total floor."""
    st = fresh(equity=92_000.0, balance=92_000.0, day_start_balance=92_000.0)
    d = evaluate({"A": 0.01}, st, LIM, cov())
    assert d.daily_floor == pytest.approx(87_000.0)
    assert d.binding_floor == pytest.approx(90_000.0), "total floor must bind"


def test_limits_do_not_grow_with_the_account():
    """After +50%, the allowance is still 5% of INITIAL, not of equity."""
    st = fresh(equity=150_000.0, balance=150_000.0, day_start_balance=150_000.0)
    d = evaluate({"A": 0.01}, st, LIM, cov())
    assert d.daily_floor == pytest.approx(145_000.0)   # 150k - 5% of 100k
    assert d.headroom == pytest.approx(5_000.0)


def test_floating_loss_shrinks_headroom_even_with_balance_unchanged():
    """Equity below balance means open positions are losing; headroom follows
    EQUITY, not balance."""
    st = fresh(equity=97_000.0, balance=100_000.0, day_start_balance=100_000.0)
    d = evaluate({"A": 0.01}, st, LIM, cov())
    assert d.headroom == pytest.approx(2_000.0)


# --- flatten and scale behaviour ------------------------------------------

def test_flattens_when_already_below_the_floor():
    st = fresh(equity=94_000.0, day_start_balance=100_000.0)
    d = evaluate({"A": 0.5}, st, LIM, cov())
    assert d.verdict is Verdict.FLATTEN
    assert d.target_weights == {}


def test_flattens_just_above_the_floor():
    """Inside hard_stop_headroom of the allowance, flatten before FTMO fires."""
    st = fresh(equity=95_400.0, day_start_balance=100_000.0)   # 400 of 5000 left
    d = evaluate({"A": 0.5}, st, LIM, cov())
    assert d.verdict is Verdict.FLATTEN


def test_scales_down_an_oversized_book():
    big = {s: 2.0 for s in SYMS}
    d = evaluate(big, fresh(), LIM, cov())
    assert d.verdict is Verdict.SCALED
    assert 0 < d.scale < 1
    assert d.est_tail_excursion <= d.budget_frac + 1e-9


def test_approves_a_small_book_unscaled():
    d = evaluate({"A": 0.05}, fresh(), LIM, cov())
    assert d.verdict is Verdict.APPROVED
    assert d.scale == pytest.approx(1.0)


def test_scaled_book_always_fits_the_budget():
    """Across a wide range of sizes and headrooms, the result respects budget."""
    for gross in (0.5, 2.0, 10.0, 50.0):
        for eq in (96_000.0, 98_000.0, 100_000.0, 130_000.0):
            st = fresh(equity=eq, balance=eq, day_start_balance=eq)
            d = evaluate({s: gross / 4 for s in SYMS}, st, LIM, cov())
            if d.verdict in (Verdict.APPROVED, Verdict.SCALED):
                assert d.est_tail_excursion <= d.budget_frac + 1e-9, (gross, eq)


def test_gross_cap_is_enforced():
    lim = GuardLimits(initial_balance=100_000.0, max_gross=1.0,
                      tail_sigmas=0.0001)  # make tail non-binding
    d = evaluate({s: 5.0 for s in SYMS}, fresh(), lim, cov())
    gross = sum(abs(w) for w in d.target_weights.values())
    assert gross <= lim.max_gross + 1e-9


# --- tail estimation -------------------------------------------------------

def test_diversification_lowers_the_tail_estimate():
    w = {s: 0.25 for s in SYMS}
    uncorr = portfolio_tail_excursion(w, cov(corr=0.0), 3.0)
    corr = portfolio_tail_excursion(w, cov(corr=0.99), 3.0)
    assert uncorr < corr, "a correlated book must be charged a larger tail"


def test_tail_scales_with_sigma_multiple():
    w = {s: 0.25 for s in SYMS}
    a = portfolio_tail_excursion(w, cov(), 1.0)
    b = portfolio_tail_excursion(w, cov(), 3.0)
    assert b == pytest.approx(3 * a)


def test_unknown_symbols_are_ignored_not_crashed():
    assert portfolio_tail_excursion({"ZZZ": 1.0}, cov(), 3.0) == 0.0
    d = evaluate({"ZZZ": 1.0}, fresh(), LIM, cov())
    assert d.verdict is Verdict.APPROVED


def test_empty_target_is_handled():
    d = evaluate({}, fresh(), LIM, cov())
    assert d.verdict is Verdict.APPROVED
    assert d.target_weights == {}
