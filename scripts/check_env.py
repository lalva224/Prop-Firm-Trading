#!/usr/bin/env python3
"""Verify .env is present, parseable, gitignored, and complete enough to run.

    python3 scripts/check_env.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.config import ENV_PATH, AccountConfig, load_env, platform_status

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def line(status: str, msg: str) -> None:
    print(f"[{status}] {msg}")


def main() -> int:
    print("\n=== .env check ===\n")
    if not ENV_PATH.exists():
        line(BAD, f"{ENV_PATH} missing. Run: cp .env.example .env")
        return 1
    vals = load_env()
    line(OK, f"{ENV_PATH} parsed, {len(vals)} keys")

    try:
        out = subprocess.run(["git", "check-ignore", "-q", str(ENV_PATH)],
                             capture_output=True, timeout=10)
        line(OK if out.returncode == 0 else BAD,
             ".env is gitignored" if out.returncode == 0
             else ".env is NOT gitignored — secrets would be committed")
    except Exception:
        line(WARN, "could not verify gitignore (git unavailable)")

    st = platform_status()
    print()
    line(OK if st["ctrader_configured"] else WARN,
         f"cTrader credentials {'set' if st['ctrader_configured'] else 'empty'}")
    line(OK if st["mt5_configured"] else WARN,
         f"MT5 credentials {'set' if st['mt5_configured'] else 'empty'}")
    topo = st["mt5_topology"]
    if topo == "native":
        line(OK, "MT5 topology: native (this host is Windows)")
    elif topo == "remote":
        line(OK, f"MT5 topology: remote agent at {st['mt5_agent_url']}")
    elif topo == "wine":
        line(OK, "MT5 topology: Wine prefix on this host")
    else:
        line(WARN, f"MT5 topology unconfigured (this OS: {st['os']}). Set "
                   f"MT5_AGENT_URL for a remote agent, or MT5_WINE_PREFIX for Wine.")
    if not st["ctrader_configured"] and not st["mt5_configured"]:
        line(WARN, "no broker configured yet — fine until you reach live testing")
    line(OK if st["databento_configured"] else WARN,
         f"Databento key {'set' if st['databento_configured'] else 'empty'} "
         f"(research only)")

    acc = AccountConfig.from_env()
    print(f"\naccount: initial ${acc.initial_balance:,.0f}  "
          f"daily limit ${acc.daily_loss_cash:,.0f} ({acc.max_daily_loss:.0%})  "
          f"floor ${acc.floor_cash:,.0f} ({acc.max_total_loss:.0%})")
    print(f"target vol {acc.target_vol:.0%}  "
          f"rebalance {acc.rebalance_time} {acc.rebalance_tz}")
    print("\nreminder: both limits are fractions of the INITIAL balance and never "
          "move as the account grows.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
