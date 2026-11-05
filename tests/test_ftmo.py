"""Tests for the FTMO rule engine.

These encode the three rules that are easiest to implement wrongly:
limits are fractions of the INITIAL balance, they are evaluated on intraday
equity including floating P&L, and the daily limit runs from start-of-day.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.ftmo import (
    FTMORules,
    brownian_adverse,
    daily_paths,
    run_funded,
    run_phase,
)

R = FTMORules()


def test_passes_on_reaching_target_after_min_days():
    rets = np.full(20, 0.01)          # +1%/day reaches +10% around day 10
    res = run_phase(rets, rets, 0.10, R)
    assert res.outcome == "pass"
    assert res.days >= R.min_trading_days


def test_min_trading_days_blocks_an_early_pass():
    rets = np.array([0.11] + [0.0] * 10)   # target hit on day 1
    res = run_phase(rets, rets, 0.10, R)
    assert res.days >= R.min_trading_days, "passed before the minimum trading days"


def test_daily_limit_is_fraction_of_initial_balance_not_equity():
    """After +50% profit, a 5% move on current equity is 7.5% of initial and
    must breach. A limit coded against current equity would survive."""
    rets = np.concatenate([np.full(10, 0.0414), [-0.05]])  # ~+50% then -5%
    res = run_phase(rets, rets, 9.99, R)     # unreachable target
    assert res.outcome == "breach_daily", res.outcome


def test_intraday_floating_loss_breaches_even_if_close_is_flat():
    rets = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
    adverse = np.array([0.0, 0.0, -0.06, 0.0, 0.0])   # -6% intraday, closes flat
    assert run_phase(rets, adverse, 0.10, R, use_intraday=True).outcome == "breach_daily"
    assert run_phase(rets, adverse, 0.10, R, use_intraday=False).outcome == "timeout"


def test_static_floor_does_not_move_up_with_profit():
    """Earn +8%, then give back 15% gradually. Equity ends at ~0.918, above the
    0.90 floor, so a STATIC floor must NOT breach. A trailing floor would."""
    rets = np.concatenate([np.full(8, 0.00965), np.full(40, -0.00435)])
    res = run_phase(rets, rets, 9.99, R)
    assert res.outcome == "timeout", res.outcome


def test_adverse_is_never_better_than_the_close():
    idx = pd.date_range("2020-01-01", periods=60, freq="B")
    rng = np.random.default_rng(1)
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (60, 3)), axis=0)),
                         index=idx, columns=list("ABC"))
    ohlc = {"close": close, "high": close * 1.01, "low": close * 0.99}
    pos = pd.DataFrame(0.3, index=idx, columns=list("ABC"))
    ret, adv = daily_paths(pos, ohlc)
    assert (adv <= ret + 1e-12).all()


def test_brownian_adverse_sits_between_close_and_worst_case():
    idx = pd.date_range("2020-01-01", periods=400, freq="B")
    rng = np.random.default_rng(5)
    ret = pd.Series(rng.normal(0.0003, 0.008, 400), index=idx)
    b = brownian_adverse(ret)
    assert (b <= ret + 1e-12).all(), "brownian must not be better than the close"
    assert b.min() > -0.5, "brownian estimate is implausibly extreme"


def test_funded_payouts_require_withdrawals_to_exist():
    rets = np.full(200, 0.001)
    paid, _ = run_funded(rets, rets, R)
    assert paid > 0
    never = FTMORules(first_payout_days=10_000, payout_every=10_000)
    paid_never, _ = run_funded(rets, rets, never)
    assert paid_never == 0.0, "unwithdrawn profit must not count as paid"


def test_funded_split_is_80_percent_before_scaling():
    rets = np.concatenate([np.full(13, 0.0), [0.10], np.zeros(5)])
    paid, _ = run_funded(rets, rets, R, model_scaling=False)
    assert paid == pytest.approx(0.80 * 0.10, rel=1e-6)
