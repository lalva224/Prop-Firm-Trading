#!/usr/bin/env python3
"""Verify the live prop-firm rules still match what the model assumes.

    python3 scripts/check_rules.py                      # staleness only
    python3 scripts/check_rules.py --dashboard objs.txt # compare against the account page
    python3 scripts/check_rules.py --verified-today     # record a human re-verification
    python3 scripts/check_rules.py --archive            # snapshot today's rules

Exit 0 = safe to trade. Exit 1 = HALT.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.rules_watch import (
    FTMO_SWING_2STEP,
    archive,
    check,
    from_cash_amounts,
    parse_dashboard,
)

CHECKLIST = [
    ("Phase 1 profit target", "10% ($10,000 on $100k)"),
    ("Phase 2 profit target", "5% ($5,000)"),
    ("Max daily loss", "5% of INITIAL balance ($5,000) - not of current equity"),
    ("Max total loss", "10% of INITIAL ($10,000), STATIC floor"),
    ("Drawdown type", "STATIC, not trailing  <- most damaging if changed"),
    ("Minimum trading days", "4 per phase"),
    ("Time limit", "none"),
    ("Profit split", "80%, scaling to 90%"),
    ("Consistency rule", "NONE  <- trend profits are lumpy; a rule here hurts"),
    ("Weekend holding", "ALLOWED (Swing). Standard forbids it once funded"),
    ("News restriction", "NONE (Swing). Standard has a 2-minute window"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dashboard", help="text file of the account objectives")
    ap.add_argument("--account-size", type=float, default=100_000)
    ap.add_argument("--max-age-days", type=int, default=30)
    ap.add_argument("--verified-today", action="store_true")
    ap.add_argument("--archive", action="store_true")
    a = ap.parse_args()

    rules = FTMO_SWING_2STEP
    if a.verified_today:
        rules = replace(rules, verified_on=date.today().isoformat())
        src = Path("src/tcs/rules_watch.py")
        src.write_text(re.sub(r'verified_on="\d{4}-\d{2}-\d{2}"',
                              f'verified_on="{date.today().isoformat()}"',
                              src.read_text(), count=1))
        print(f"recorded verification on {date.today().isoformat()}\n")

    observed = None
    if a.dashboard:
        raw = Path(a.dashboard).read_text()
        observed = from_cash_amounts(parse_dashboard(raw), a.account_size)
        print("parsed from the dashboard:")
        for k, v in sorted(observed.items()):
            print(f"  {k} = {v}")
        print()

    print(f"=== rules: {rules.firm} / {rules.product} ===")
    print(f"fingerprint {rules.fingerprint()}   verified {rules.verified_on}\n")
    res = check(rules, observed, a.max_age_days)
    print(res.report())

    if a.archive:
        print(f"\narchived -> {archive(rules)}")

    if res.must_halt:
        print("\n" + "=" * 68)
        print("RE-VERIFY EACH OF THESE AGAINST THE LIVE DASHBOARD:")
        for name, expect in CHECKLIST:
            print(f"  [ ] {name:<24} {expect}")
        print("\nThen: python3 scripts/check_rules.py --verified-today --archive")
        print("=" * 68)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
