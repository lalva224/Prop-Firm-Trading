"""Tests for the file bridge and the reconciliation log.

The properties tested here are the ones that make a file-based protocol safe:
atomic writes, replay protection via the nonce, staleness detection, and
sizing arithmetic that matches the EA exactly.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.bridge import Bridge, STATE_FILE, SYMBOLS_FILE, TARGETS_CSV
from tcs.guard import GuardDecision, Verdict
from tcs.reconcile import build_row, compare


def decision(weights, verdict=Verdict.APPROVED, scale=1.0) -> GuardDecision:
    return GuardDecision(verdict=verdict, scale=scale, target_weights=weights,
                         binding_floor=190_000.0, headroom=10_000.0,
                         budget_frac=0.025)


def write_state(d: Path, **kw):
    payload = dict(schema=1, ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   login=1, connected=True, equity=200_000.0, balance=200_000.0,
                   day_start_balance=200_000.0, margin_free=0.0, nonce_seen="",
                   positions=[], last_actions=[], errors=[])
    payload.update(kw)
    (d / STATE_FILE).write_text(json.dumps(payload))


def write_symbols(d: Path, specs: dict):
    (d / SYMBOLS_FILE).write_text(json.dumps({"schema": 1, "symbols": specs}))


# --- target publishing ----------------------------------------------------

def test_csv_is_what_the_ea_reads_and_carries_the_metadata(tmp_path):
    b = Bridge(tmp_path)
    nonce = b.write_targets(decision({"A": 0.1, "B": -0.2}), login=42)
    text = (tmp_path / TARGETS_CSV).read_text()
    header, *rows = text.strip().splitlines()
    assert f"nonce={nonce}" in header and "login=42" in header
    assert "flatten=0" in header
    assert sorted(rows) == ["A,0.10000000", "B,-0.20000000"]


def test_each_publish_gets_a_fresh_nonce(tmp_path):
    b = Bridge(tmp_path)
    assert b.write_targets(decision({"A": 0.1}), 1) != b.write_targets(decision({"A": 0.1}), 1)


def test_refused_and_flatten_both_publish_flatten(tmp_path):
    """REFUSED must not leave an open book unmanaged - it means flatten."""
    for v in (Verdict.REFUSED, Verdict.FLATTEN):
        b = Bridge(tmp_path)
        b.write_targets(decision({}, verdict=v, scale=0.0), 1)
        assert "flatten=1" in (tmp_path / TARGETS_CSV).read_text().splitlines()[0]


def test_zero_weights_are_not_published(tmp_path):
    b = Bridge(tmp_path)
    b.write_targets(decision({"A": 0.1, "B": 0.0}), 1)
    rows = (tmp_path / TARGETS_CSV).read_text().strip().splitlines()[1:]
    assert rows == ["A,0.10000000"]


def test_no_tmp_files_left_behind(tmp_path):
    b = Bridge(tmp_path)
    b.write_targets(decision({"A": 0.1}), 1)
    assert not list(tmp_path.glob("*.tmp")), "atomic write leaked a temp file"


# --- health and staleness -------------------------------------------------

def test_missing_files_are_unhealthy_not_an_exception(tmp_path):
    ok, problems = Bridge(tmp_path).health()
    assert not ok and any("heartbeat" in p for p in problems)


def test_stale_state_is_detected(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    write_state(tmp_path, ts_utc=old)
    (tmp_path / "tcs_heartbeat.json").write_text(json.dumps({"ts_utc": old}))
    ok, problems = Bridge(tmp_path, max_state_age_s=60).health()
    assert not ok and any("old" in p for p in problems)


def test_disconnected_terminal_is_reported(tmp_path):
    write_state(tmp_path, connected=False)
    (tmp_path / "tcs_heartbeat.json").write_text(json.dumps(
        {"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    ok, problems = Bridge(tmp_path).health()
    assert not ok and any("not connected" in p for p in problems)


def test_corrupt_json_does_not_crash(tmp_path):
    (tmp_path / STATE_FILE).write_text("{ this is not json")
    st = Bridge(tmp_path).read_state()
    assert st.equity == 0.0 and not st.connected


def test_unhealthy_bridge_yields_a_disconnected_account_state(tmp_path):
    """So the guard refuses rather than sizing against zeros."""
    acct = Bridge(tmp_path).to_account_state()
    assert acct.connected is False


# --- sizing must match the EA --------------------------------------------

SPEC = {"XAUUSD": dict(bid=2650.0, tick_value=1.0, tick_size=0.01,
                       volume_step=0.01, volume_min=0.01, volume_max=50.0,
                       notional_per_lot=265_000.0)}


def test_expected_lots_matches_the_formula(tmp_path):
    write_symbols(tmp_path, SPEC)
    lots, notes = Bridge(tmp_path).expected_lots({"XAUUSD": 0.5}, 200_000.0)
    # 0.5 * 200000 / 265000 = 0.3773 -> floored to the 0.01 step
    assert lots["XAUUSD"] == pytest.approx(0.37, abs=1e-9)
    assert not notes


def test_rounding_floors_and_never_oversizes(tmp_path):
    write_symbols(tmp_path, SPEC)
    lots, _ = Bridge(tmp_path).expected_lots({"XAUUSD": 0.99}, 200_000.0)
    raw = 0.99 * 200_000.0 / 265_000.0
    assert lots["XAUUSD"] <= raw + 1e-12


def test_below_minimum_volume_is_dropped_with_a_note(tmp_path):
    write_symbols(tmp_path, SPEC)
    lots, notes = Bridge(tmp_path).expected_lots({"XAUUSD": 0.00001}, 200_000.0)
    assert "XAUUSD" not in lots
    assert any("below the minimum" in n for n in notes)


def test_sign_is_preserved(tmp_path):
    write_symbols(tmp_path, SPEC)
    lots, _ = Bridge(tmp_path).expected_lots({"XAUUSD": -0.5}, 200_000.0)
    assert lots["XAUUSD"] < 0


def test_missing_spec_is_flagged_not_guessed(tmp_path):
    write_symbols(tmp_path, {})
    lots, notes = Bridge(tmp_path).expected_lots({"NOPE": 0.5}, 200_000.0)
    assert not lots and any("no broker spec" in n for n in notes)


def test_volume_steps_feed_the_reconciliation_tolerance(tmp_path):
    write_symbols(tmp_path, SPEC)
    assert Bridge(tmp_path).volume_steps()["XAUUSD"] == pytest.approx(0.01)


# --- reconciliation -------------------------------------------------------

def test_wrong_sign_is_called_out_explicitly():
    divs = compare({"A": 1.0}, {"A": -1.0}, 0.01)
    assert len(divs) == 1 and "WRONG SIGN" in divs[0].reason


def test_within_tolerance_is_not_a_divergence():
    assert compare({"A": 0.50}, {"A": 0.49}, 0.01) == []


def test_per_symbol_tolerance_is_respected():
    assert compare({"A": 1.0}, {"A": 0.5}, {"A": 1.0}) == []
    assert compare({"A": 1.0}, {"A": 0.5}, {"A": 0.01}) != []


def test_row_counts_and_summarises():
    row = build_row("n1", {"A": 1.0, "B": 2.0}, {"A": 1.0}, 200_000.0, 200_000.0,
                    10_000.0, "approved", 1.0, 0.01)
    assert row.n_divergent == 1 and row.max_abs_diff == pytest.approx(2.0)
    assert row.sim_gross == pytest.approx(3.0) and row.live_gross == pytest.approx(1.0)
