"""Tests for rule-change detection.

The failure this guards against is silent: FTMO changes a limit, every EV
number becomes wrong, and nothing in the system notices. So the tests focus on
"does it halt when it should", not on parsing elegance.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.rules_watch import (
    CRITICAL,
    FTMO_SWING_2STEP as R,
    check,
    compare,
    from_cash_amounts,
    parse_dashboard,
)


def test_unchanged_rules_are_safe():
    r = check(R, observed={"max_daily_loss": 0.05, "max_total_loss": 0.10})
    assert not r.diffs and not r.must_halt


def test_changed_loss_limit_halts():
    r = check(R, observed={"max_daily_loss": 0.04})
    assert r.must_halt
    assert any(d.critical and d.field == "max_daily_loss" for d in r.diffs)


def test_introduced_consistency_rule_halts():
    """FTMO has none today. If one appears, lumpy trend profits could be
    denied at payout - which the EV model does not price."""
    r = check(R, observed={"consistency_rule": 0.40})
    assert r.must_halt


def test_weekend_holding_withdrawn_halts():
    """Losing weekend holding turns a multi-week strategy into a different
    one. It must never be treated as a minor change."""
    r = check(R, observed={"weekend_holding": False})
    assert r.must_halt


def test_drawdown_type_change_is_critical():
    """Static -> trailing is the single most damaging possible change:
    measured EV collapsed from 7.4x to 0.4x on trailing-drawdown firms."""
    r = check(R, observed={"drawdown_type": "trailing"})
    assert r.must_halt


def test_split_change_is_critical():
    assert check(R, observed={"profit_split": 0.70}).must_halt


def test_minor_change_does_not_halt():
    r = check(R, observed={"min_trading_days": 5})
    assert r.diffs and not any(d.critical for d in r.diffs)
    assert not r.must_halt


def test_stale_rules_halt_even_when_identical():
    """Rules verified months ago are not evidence of anything today."""
    old = replace(R, verified_on=str(date.today() - timedelta(days=90)))
    r = check(old, observed={"max_daily_loss": 0.05}, max_age_days=30)
    assert r.stale and r.must_halt


def test_unparseable_date_is_treated_as_stale():
    r = check(replace(R, verified_on="never"), max_age_days=30)
    assert r.stale and r.must_halt


def test_fingerprint_ignores_verification_date():
    assert R.fingerprint() == replace(R, verified_on="2020-01-01").fingerprint()


def test_fingerprint_changes_with_any_rule():
    assert R.fingerprint() != replace(R, max_total_loss=0.08).fingerprint()


def test_every_critical_field_actually_halts():
    """Guards the CRITICAL list itself against future edits."""
    alt = {"phase_targets": (0.08, 0.05), "max_daily_loss": 0.04,
           "max_total_loss": 0.08, "drawdown_type": "trailing",
           "profit_split": 0.70, "consistency_rule": 0.4,
           "weekend_holding": False}
    for f in CRITICAL:
        assert check(R, observed={f: alt[f]}).must_halt, f


def test_parses_the_real_dashboard_text():
    """Text copied from the account page this project is built against."""
    txt = """Objectives
             Minimum 2 Trading Days 0
             Max Daily Loss: -$5,000  $0.00 (0%)
             Max Loss: -$10,000  $0.00 (0%)
             Profit Target: $5,000  $0.00 (0%)"""
    obs = from_cash_amounts(parse_dashboard(txt), account_size=100_000)
    assert obs["max_daily_loss"] == pytest.approx(0.05)
    assert obs["max_total_loss"] == pytest.approx(0.10)
    assert obs["min_trading_days"] == 2


def test_dashboard_mismatch_is_detected():
    txt = "Max Daily Loss: -$4,000   Max Loss: -$10,000"
    obs = from_cash_amounts(parse_dashboard(txt), account_size=100_000)
    assert check(R, observed=obs).must_halt
