#!/usr/bin/env python3
"""Mock MT5 EA — exercises the whole Linux side with no broker at all.

Reads tcs_targets.csv exactly as TcsBridge.mq5 does, applies the same sizing
and rounding arithmetic, and writes tcs_state.json / tcs_heartbeat.json /
tcs_symbols.json back. Nothing connects to a broker.

Why this exists: Wine, MT5 and a demo account are three independent things
that can each take an afternoon. This lets the guard, the bridge, the
orchestrator and the reconciliation log all be tested today, so that when the
real EA is attached the only new variable is MT5 itself.

    python3 scripts/mock_ea.py --dir /tmp/tcs --watch
    python3 scripts/mock_ea.py --dir /tmp/tcs --fail-symbol NATGAS   # inject a rejection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# A plausible broker spec sheet. notional_per_lot = bid * tick_value / tick_size,
# the same formula the EA uses.
SPECS = {
    "^GSPC":    dict(bid=5800.0, tick_value=0.10, tick_size=0.10, contract_size=1,   volume_step=0.10, volume_min=0.10, volume_max=100),
    "US500":    dict(bid=5800.0, tick_value=0.10, tick_size=0.10, contract_size=1,   volume_step=0.10, volume_min=0.10, volume_max=100),
    "US100":    dict(bid=20500.0, tick_value=0.10, tick_size=0.10, contract_size=1,  volume_step=0.10, volume_min=0.10, volume_max=100),
    "GER40":    dict(bid=19500.0, tick_value=0.10, tick_size=0.10, contract_size=1,  volume_step=0.10, volume_min=0.10, volume_max=100),
    "XAUUSD":   dict(bid=2650.0, tick_value=1.00, tick_size=0.01, contract_size=100, volume_step=0.01, volume_min=0.01, volume_max=50),
    "XAGUSD":   dict(bid=31.0,   tick_value=5.00, tick_size=0.01, contract_size=5000, volume_step=0.01, volume_min=0.01, volume_max=50),
    "USOIL":    dict(bid=71.0,   tick_value=10.0, tick_size=0.01, contract_size=1000, volume_step=0.01, volume_min=0.01, volume_max=50),
    "NATGAS":   dict(bid=2.9,    tick_value=10.0, tick_size=0.001, contract_size=10000, volume_step=0.10, volume_min=1.00, volume_max=50),
    "EURUSD=X": dict(bid=1.08,   tick_value=1.00, tick_size=0.00001, contract_size=100000, volume_step=0.01, volume_min=0.01, volume_max=100),
    "EURUSD":   dict(bid=1.08,   tick_value=1.00, tick_size=0.00001, contract_size=100000, volume_step=0.01, volume_min=0.01, volume_max=100),
}


def synth_spec(sym: str) -> dict:
    """Plausible spec for a symbol not in the table above.

    Lets the mock drive any universe. Prices are arbitrary - what matters for
    testing is that volume_step and volume_min exist, because rounding is the
    bug class this harness is built to catch.
    """
    h = abs(hash(sym))
    bid = 20.0 + (h % 400)
    return dict(bid=float(bid), tick_value=1.0, tick_size=0.01,
                contract_size=100.0, volume_step=0.01, volume_min=0.01,
                volume_max=200.0, synthetic=True)


def iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def npl(spec: dict) -> float:
    return spec["bid"] * spec["tick_value"] / spec["tick_size"]


def round_step(raw: float, spec: dict) -> float:
    """Identical to RoundToStep in the EA: floor to step, zero below minimum."""
    step, vmin, vmax = spec["volume_step"], spec["volume_min"], spec["volume_max"]
    mag = abs(raw)
    r = int(mag / step + 1e-9) * step
    if r < vmin:
        return 0.0
    return -min(r, vmax) if raw < 0 else min(r, vmax)


def atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class MockEA:
    def __init__(self, d: Path, equity: float, fail: set[str], reject_all: bool,
                 unknown: set[str] | None = None, day_start: float | None = None):
        self.d = d
        self.equity = equity
        self.balance = equity
        # Settable independently so an intraday loss can be simulated: a day
        # that OPENED higher than current equity has already consumed part of
        # its daily allowance. That is the state the flatten path exists for.
        self.day_start = day_start if day_start is not None else equity
        self.fail = fail
        self.reject_all = reject_all
        self.unknown = unknown or set()
        self.positions: dict[str, float] = {}
        self.nonce_seen = ""
        self.errors: list[str] = []
        self.actions: list[dict] = []
        self.d.mkdir(parents=True, exist_ok=True)

    def read_targets(self):
        p = self.d / "tcs_targets.csv"
        if not p.exists():
            return None
        lines = [l for l in p.read_text().splitlines() if l.strip()]
        if not lines:
            return None
        header = lines[0]
        meta = dict(
            kv.split("=", 1) for kv in header.lstrip("# ").split() if "=" in kv
        )
        targets = {}
        for l in lines[1:]:
            if l.startswith("#"):
                continue
            sym, _, w = l.partition(",")
            targets[sym.strip()] = float(w)
        return meta, targets

    def step(self) -> bool:
        got = self.read_targets()
        if not got:
            self.write_all({})
            return False
        meta, targets = got
        if meta.get("nonce") == self.nonce_seen:
            self.write_all(targets)
            return False

        self.errors, self.actions = [], []

        if meta.get("flatten") == "1":
            for sym in list(self.positions):
                self.actions.append({"symbol": sym, "action": "flatten",
                                     "volume": abs(self.positions[sym]), "retcode": 10009})
            self.positions.clear()
            self.nonce_seen = meta.get("nonce", "")
            self.write_all(targets)
            return True

        for sym in list(self.positions):
            if sym not in targets:
                self.actions.append({"symbol": sym, "action": "close_untargeted",
                                     "volume": abs(self.positions[sym]), "retcode": 10009})
                del self.positions[sym]

        for sym, w in targets.items():
            if sym in self.unknown:
                self.errors.append(f"unknown symbol {sym} - check the broker's naming")
                continue
            spec = SPECS.get(sym) or synth_spec(sym)
            want = round_step(w * self.equity / npl(spec), spec)
            if want == 0.0 and abs(w) > 1e-9:
                self.errors.append(
                    f"{sym} target {w:.6f} rounds below minimum volume - skipped")
            have = self.positions.get(sym, 0.0)
            delta = want - have
            if abs(delta) < spec["volume_step"] - 1e-9:
                continue
            if self.reject_all or sym in self.fail:
                self.errors.append(f"{sym} order rejected retcode=10016 (injected)")
                self.actions.append({"symbol": sym, "action": "rejected",
                                     "volume": abs(delta), "retcode": 10016})
                continue
            self.positions[sym] = want
            if want == 0.0:
                self.positions.pop(sym, None)
            self.actions.append({"symbol": sym,
                                 "action": "buy" if delta > 0 else "sell",
                                 "volume": round(abs(delta), 4), "retcode": 10009})

        self.nonce_seen = meta.get("nonce", "")
        self.write_all(targets)
        return True

    def write_all(self, targets: dict) -> None:
        atomic(self.d / "tcs_heartbeat.json", json.dumps(
            {"ts_utc": iso(), "login": 9999999, "connected": True}))
        atomic(self.d / "tcs_state.json", json.dumps({
            "schema": 1, "ts_utc": iso(), "login": 9999999, "connected": True,
            "equity": round(self.equity, 2), "balance": round(self.balance, 2),
            "day_start_balance": round(self.day_start, 2),
            "margin_free": round(self.equity * 0.8, 2),
            "nonce_seen": self.nonce_seen,
            "positions": [{"symbol": s, "net_lots": round(v, 4), "profit": 0.0}
                          for s, v in sorted(self.positions.items())],
            "last_actions": self.actions, "errors": self.errors,
        }, indent=2))
        syms = {}
        for s in targets:
            if s in self.unknown:
                continue
            sp = SPECS.get(s) or synth_spec(s)
            syms[s] = {**sp, "notional_per_lot": round(npl(sp), 4)}
        atomic(self.d / "tcs_symbols.json",
               json.dumps({"schema": 1, "symbols": syms}, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--equity", type=float, default=200_000.0)
    ap.add_argument("--day-start", type=float, default=None,
                    help="balance at the start of the server day; set above "
                         "--equity to simulate an intraday loss")
    ap.add_argument("--fail-symbol", action="append", default=[])
    ap.add_argument("--reject-all", action="store_true")
    ap.add_argument("--unknown-symbol", action="append", default=[],
                    help="pretend the broker does not list this symbol")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()

    ea = MockEA(Path(a.dir), a.equity, set(a.fail_symbol), a.reject_all,
                set(a.unknown_symbol), a.day_start)
    print(f"mock EA on {a.dir}  equity={a.equity:,.0f}  "
          f"fail={sorted(ea.fail) or 'none'}  reject_all={a.reject_all}")
    if not a.watch:
        acted = ea.step()
        print(f"acted={acted}  positions={ea.positions}")
        for e in ea.errors:
            print("  error:", e)
        return 0
    try:
        while True:
            if ea.step():
                print(f"[{iso()}] executed nonce={ea.nonce_seen} "
                      f"positions={len(ea.positions)} errors={len(ea.errors)}")
                for e in ea.errors:
                    print("   ", e)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
