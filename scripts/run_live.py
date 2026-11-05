#!/usr/bin/env python3
"""Daily rebalance orchestrator: strategy -> guard -> bridge -> reconcile.

    # dry run: compute and print, write nothing
    python3 scripts/run_live.py --bridge-dir /tmp/tcs --dry-run

    # full cycle against the mock EA (no broker involved)
    python3 scripts/mock_ea.py --dir /tmp/tcs --watch &
    python3 scripts/run_live.py --bridge-dir /tmp/tcs

    # reconciliation history
    python3 scripts/run_live.py --summary

Order of operations is deliberate: READ broker state first, so the guard sizes
against real equity and real floating P&L rather than against an assumption.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from tcs import reconcile
from tcs.backtest import BacktestConfig, run
from tcs.bridge import Bridge
from tcs.config import AccountConfig
from tcs.data import load_ohlc
from tcs.guard import GuardLimits, Verdict, evaluate
from tcs.signals import ewmac_blend
from tcs.universe import UNIVERSES


def strategy_weights(ohlc, target_vol: float, cost_bps: float = 3.0):
    """Today's target weights, plus the covariance the guard needs."""
    px = ohlc["close"]
    cfg = BacktestConfig(target_ann_vol=target_vol, max_leverage=12.0,
                         cost_bps=cost_bps)
    positions = run(px, lambda p: ewmac_blend(p, smooth=10), cfg).positions
    latest = positions.iloc[-1]
    weights = {s: float(w) for s, w in latest.items() if abs(w) > 1e-9}
    # Daily covariance over the last year - the horizon the daily loss limit
    # cares about.
    cov = px.pct_change().tail(252).cov()
    return weights, cov, positions.index[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge-dir", help="MT5 MQL5/Files directory")
    ap.add_argument("--universe", default="etf", choices=list(UNIVERSES))
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--login", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=float, default=30.0,
                    help="seconds to wait for the EA to echo the nonce")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--log", default=str(reconcile.DEFAULT_LOG))
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    if a.summary:
        print("\n=== reconciliation history ===\n")
        print(reconcile.summarise(a.log))
        return 0

    if not a.bridge_dir:
        print("--bridge-dir is required (or use --summary)")
        return 2

    acc = AccountConfig.from_env()
    bridge = Bridge(a.bridge_dir)

    # 1. Broker state FIRST, so sizing uses real equity and floating P&L.
    healthy, problems = bridge.health()
    state = bridge.read_state()
    print(f"\nbroker: equity {state.equity:,.2f}  balance {state.balance:,.2f}  "
          f"day-open {state.day_start_balance:,.2f}  "
          f"state age {state.age_seconds:.0f}s  heartbeat {bridge.heartbeat_age():.0f}s")
    if not healthy:
        for p in problems:
            print(f"  UNHEALTHY: {p}")

    # 2. Strategy.
    tickers = list(UNIVERSES[a.universe])
    ohlc = load_ohlc(tickers, start=a.start,
                     cache_key=f"{a.universe}_ohlc_{a.start[:4]}")
    weights, cov, asof = strategy_weights(ohlc, acc.target_vol)
    print(f"strategy: {len(weights)} positions as of {asof.date()}  "
          f"gross {sum(abs(w) for w in weights.values()):.2f}")

    # 3. Guard.
    limits = GuardLimits(initial_balance=acc.initial_balance,
                         max_daily_loss=acc.max_daily_loss,
                         max_total_loss=acc.max_total_loss)
    acct = bridge.to_account_state()
    decision = evaluate(weights, acct, limits, cov)
    print("\n--- guard ---")
    print(decision.report())

    if a.dry_run:
        exp, notes = bridge.expected_lots(decision.target_weights, state.equity or acc.initial_balance)
        print("\n--- expected lots (dry run) ---")
        if not exp and not notes:
            print("  no broker symbol specs yet; attach the EA (or mock) once")
        for s, v in sorted(exp.items()):
            print(f"  {s:<12}{v:>9.2f}")
        for n in notes:
            print(f"  note: {n}")
        print("\ndry run: nothing written")
        return 0

    if decision.verdict is Verdict.REFUSED:
        print("\nguard REFUSED - publishing a flatten instruction")

    # 4. Publish.
    nonce = bridge.write_targets(decision, login=a.login or state.login)
    print(f"\npublished nonce={nonce}")

    # 5. Wait for the EA to echo it.
    deadline = time.time() + a.wait
    acted = False
    while time.time() < deadline:
        if bridge.read_state().nonce_seen == nonce:
            acted = True
            break
        time.sleep(1.0)
    print(f"EA acted: {acted}" + ("" if acted else f" (no echo within {a.wait:.0f}s)"))

    # 6. Reconcile.
    after = bridge.read_state()
    expected, notes = bridge.expected_lots(
        decision.target_weights, after.equity or state.equity or acc.initial_balance)
    if not acted:
        notes.append(f"EA did not echo nonce {nonce} within {a.wait:.0f}s")
    notes += [f"EA error: {e}" for e in after.errors]
    notes += problems

    row = reconcile.build_row(
        nonce=nonce,
        simulated_lots=expected,
        live_lots=after.positions,
        equity=after.equity,
        day_start_balance=after.day_start_balance,
        headroom=decision.headroom,
        guard_verdict=decision.verdict.value,
        guard_scale=decision.scale,
        tolerance=bridge.volume_steps() or 0.01,
        notes=notes,
    )
    path = reconcile.log_run(row, a.log)

    print("\n--- reconciliation ---")
    print(f"{'symbol':<12}{'expected':>10}{'live':>10}{'diff':>10}  reason")
    print("-" * 64)
    allsyms = sorted(set(expected) | set(after.positions))
    divmap = {d["symbol"]: d for d in row.divergences}
    for s in allsyms:
        e, l = expected.get(s, 0.0), after.positions.get(s, 0.0)
        d = divmap.get(s)
        print(f"{s:<12}{e:>10.2f}{l:>10.2f}{l - e:>10.2f}  "
              f"{d['reason'] if d else 'ok'}")
    print(f"\ndivergences: {row.n_divergent}   worst {row.max_abs_diff:.4f} lots")
    for n in row.notes:
        print(f"  note: {n}")
    print(f"logged -> {path}")
    return 0 if row.n_divergent == 0 and acted else 1


if __name__ == "__main__":
    raise SystemExit(main())
