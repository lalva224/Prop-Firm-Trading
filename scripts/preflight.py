#!/usr/bin/env python3
"""One command: is the whole stack alive and safe to trade?

    python3 scripts/preflight.py --bridge-dir ~/mt5-bridge --vm mt5-win

Checks, in dependency order, so the first failure is the actual cause rather
than a downstream symptom:

    config -> VM running -> share writable -> EA heartbeat -> broker connected
    -> account matches .env -> guard has headroom -> reconciliation history

Exit code 0 means every check passed. Anything else means do not trade.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs import reconcile
from tcs.bridge import Bridge
from tcs.config import AccountConfig, ENV_PATH, load_env

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "
_fails: list[str] = []
_warns: list[str] = []


def line(status: str, msg: str, detail: str = "") -> None:
    print(f"[{status}] {msg}" + (f"\n         {detail}" if detail else ""))
    if status == BAD:
        _fails.append(msg)
    elif status == WARN:
        _warns.append(msg)


def check_vm(name: str) -> bool:
    if not shutil.which("virsh"):
        line(WARN, "virsh not installed - skipping VM check "
                   "(fine if MT5 runs elsewhere)")
        return True
    try:
        out = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", name],
                             capture_output=True, text=True, timeout=15)
    except Exception as e:
        line(WARN, f"could not query VM '{name}': {e}")
        return True
    state = out.stdout.strip()
    if out.returncode != 0:
        line(WARN, f"VM '{name}' not defined on this host", out.stderr.strip()[:200])
        return True
    if state == "running":
        line(OK, f"VM '{name}' is running")
        return True
    line(BAD, f"VM '{name}' state is '{state}'",
         f"start it: virsh -c qemu:///system start {name}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge-dir", required=True)
    ap.add_argument("--vm", default="mt5-win")
    ap.add_argument("--log", default=str(reconcile.DEFAULT_LOG))
    ap.add_argument("--max-rule-age", type=int, default=30,
                    help="days before encoded prop rules are treated as stale")
    a = ap.parse_args()

    print("\n=== preflight ===\n")

    # 1. config
    if not ENV_PATH.exists():
        line(BAD, ".env missing", "cp .env.example .env")
        return 1
    load_env()
    acc = AccountConfig.from_env()
    line(OK, f".env loaded  initial ${acc.initial_balance:,.0f}  "
             f"daily ${acc.daily_loss_cash:,.0f}  floor ${acc.floor_cash:,.0f}")

    # 2. Rules. Every EV figure assumes a specific rule set; if those rules
    # changed, the model is wrong and trading on it is trading blind.
    rc = check_rules(FTMO_SWING_2STEP, max_age_days=a.max_rule_age)
    if rc.must_halt:
        line(BAD, f"prop rules unverified for {rc.age_days} days or changed",
             "run: python3 scripts/check_rules.py --dashboard <objectives.txt>")
        for d in rc.diffs:
            line(BAD, f"rule change: {d}")
    else:
        line(OK, f"prop rules verified {rc.age_days}d ago "
                 f"(fingerprint {FTMO_SWING_2STEP.fingerprint()})")

    # 3. VM
    check_vm(a.vm)

    # 3. share
    d = Path(a.bridge_dir).expanduser()
    if not d.is_dir():
        line(BAD, f"bridge dir {d} does not exist")
    else:
        probe = d / ".preflight_write_test"
        try:
            probe.write_text("ok")
            probe.unlink()
            line(OK, f"bridge dir {d} is writable")
        except OSError as e:
            line(BAD, f"bridge dir {d} is not writable: {e}")

    bridge = Bridge(d)

    # 4. heartbeat - proves the EA is alive, not merely that MT5 is a process
    hb = bridge.heartbeat_age()
    if hb == float("inf"):
        line(BAD, "no heartbeat from the EA",
             "is TcsBridge attached to a chart with Algo Trading enabled?")
    elif hb > 120:
        line(BAD, f"heartbeat is {hb:.0f}s old - the terminal is wedged",
             "a stuck modal dialog looks alive to a process check; "
             "connect to the console and dismiss it")
    else:
        line(OK, f"EA heartbeat {hb:.0f}s old")

    # 5. broker state
    st = bridge.read_state()
    healthy, problems = bridge.health()
    if not st.ts_utc:
        line(BAD, "no state file from the EA")
    else:
        line(OK if st.connected else BAD,
             f"terminal {'connected' if st.connected else 'NOT connected'} to broker  "
             f"login {st.login}  equity ${st.equity:,.2f}")
    for p in problems:
        if "heartbeat" not in p:
            line(WARN, p)

    # 6. the account must match .env, or every limit is computed wrongly
    if st.equity > 0:
        ratio = st.equity / acc.initial_balance
        if not 0.5 < ratio < 2.0:
            line(BAD, f"equity ${st.equity:,.0f} is far from "
                      f"FTMO_INITIAL_BALANCE ${acc.initial_balance:,.0f}",
                 "the guard derives BOTH loss limits from the initial balance; "
                 "a wrong value makes it think you have the wrong allowance")
        else:
            line(OK, "equity is consistent with FTMO_INITIAL_BALANCE")

    # 7. headroom
    if st.equity > 0:
        day_open = st.day_start_balance or st.balance
        daily_floor = day_open - acc.daily_loss_cash
        floor = max(acc.floor_cash, daily_floor)
        headroom = st.equity - floor
        pct = headroom / acc.daily_loss_cash if acc.daily_loss_cash else 0
        if headroom <= 0:
            line(BAD, f"NO HEADROOM: equity ${st.equity:,.2f} at/below floor ${floor:,.2f}")
        elif pct < 0.25:
            line(WARN, f"headroom ${headroom:,.2f} is only {pct:.0%} of the daily "
                       f"allowance - the guard will flatten rather than trade")
        else:
            line(OK, f"headroom ${headroom:,.2f} ({pct:.0%} of daily allowance)")

    # 8. positions and open orders
    if st.positions:
        gross = sum(abs(v) for v in st.positions.values())
        line(OK, f"{len(st.positions)} open positions, {gross:.2f} lots gross")
    else:
        line(OK, "flat")
    if st.errors:
        for e in st.errors[:5]:
            line(WARN, f"EA error: {e}")

    # 9. reconciliation history
    df = reconcile.load_log(a.log)
    if df.empty:
        line(WARN, "no reconciliation runs logged yet - run scripts/run_live.py")
    else:
        recent = df.tail(10)
        bad_runs = int((recent.n_divergent > 0).sum())
        wrong_sign = sum(
            1 for divs in recent.divergences for dd in divs
            if "WRONG SIGN" in dd.get("reason", "")
        )
        if wrong_sign:
            line(BAD, f"{wrong_sign} WRONG-SIGN divergences in the last 10 runs",
                 "live positions are opposite the target - fix before trading")
        elif bad_runs > 3:
            line(WARN, f"{bad_runs} of the last 10 runs had divergences")
        else:
            line(OK, f"last 10 runs: {10 - bad_runs} clean")

    print()
    if _fails:
        print(f"RESULT: DO NOT TRADE - {len(_fails)} check(s) failed")
        for f in _fails:
            print(f"  - {f}")
        return 1
    if _warns:
        print(f"RESULT: proceed with caution - {len(_warns)} warning(s)")
        return 0
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
