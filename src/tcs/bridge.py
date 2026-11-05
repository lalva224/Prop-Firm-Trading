"""File-based bridge between the Linux strategy and the MT5 terminal.

MT5 runs under Wine; its `MQL5/Files` directory is an ordinary Linux folder, so
both sides read and write the same JSON. For a once-a-day rebalance, latency is
irrelevant and a file handoff beats a socket: no ports, no auth, no firewall,
and every exchange is left on disk for inspection afterwards.

PROTOCOL
  tcs_targets.csv   Linux -> EA.  Desired weights plus the guard's decision.
                    CSV rather than JSON because MQL5 has no JSON parser and
                    hand-rolling one inside the EA would be the least reliable
                    code in the system. A JSON copy is written alongside it for
                    inspection, but the EA never reads that.
  state.json      EA -> Linux.  Account snapshot, positions, last fills, errors.
  heartbeat.json  EA -> Linux.  Proof the terminal is alive.

SAFETY PROPERTIES
  atomic writes   Written to .tmp then renamed, so a reader never sees a
                  half-written file.
  nonce           Every target set carries a unique nonce. The EA records the
                  nonce it acted on, so a file left on disk from yesterday can
                  never be replayed as today's instruction.
  staleness       Both sides refuse to act on data older than a threshold. A
                  wedged terminal therefore causes inaction, not stale action.
  fail flat       If anything is wrong, the intended state is no position -
                  never a frozen position nobody is managing.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .guard import GuardDecision, Verdict

SCHEMA = 1
TARGETS_FILE = "tcs_targets.json"      # audit copy, human-readable
TARGETS_CSV = "tcs_targets.csv"        # what the EA actually reads
STATE_FILE = "tcs_state.json"
HEARTBEAT_FILE = "tcs_heartbeat.json"
SYMBOLS_FILE = "tcs_symbols.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age_seconds(ts: str) -> float:
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


@dataclass
class BrokerState:
    """Parsed `state.json`. Mirrors what the EA reports."""
    ts_utc: str = ""
    login: int = 0
    equity: float = 0.0
    balance: float = 0.0
    day_start_balance: float = 0.0
    margin_free: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)
    last_actions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    nonce_seen: str = ""
    connected: bool = False

    @property
    def age_seconds(self) -> float:
        return _age_seconds(self.ts_utc)


class Bridge:
    """Linux-side endpoint. Point `directory` at MT5's MQL5/Files folder.

    Under Wine that is typically:
      ~/.mt5/drive_c/users/<user>/AppData/Roaming/MetaQuotes/Terminal/<hash>/MQL5/Files
    """

    def __init__(self, directory: str | Path, max_state_age_s: float = 120.0):
        self.dir = Path(directory).expanduser()
        self.max_state_age_s = max_state_age_s

    # --- writing ---------------------------------------------------------

    def _atomic_write(self, name: str, payload: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(tmp, path)   # atomic on POSIX
        return path

    def write_targets(
        self,
        decision: GuardDecision,
        login: int,
        nonce: str | None = None,
    ) -> str:
        """Publish the guard's decision. Returns the nonce the EA must echo."""
        nonce = nonce or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        flatten = decision.verdict in (Verdict.FLATTEN, Verdict.REFUSED)
        payload = {
            "schema": SCHEMA,
            "ts_utc": utcnow(),
            "nonce": nonce,
            "account_login": login,
            "verdict": decision.verdict.value,
            "scale": round(decision.scale, 6),
            # REFUSED means "do not trade", which for an already-open book means
            # flatten: an unmanaged position is worse than no position.
            "flatten_all": flatten,
            "targets": [
                {"symbol": s, "weight": round(w, 8)}
                for s, w in sorted(decision.target_weights.items())
                if abs(w) > 1e-9
            ],
            "guard": {
                "binding_floor": round(decision.binding_floor, 2),
                "headroom": round(decision.headroom, 2),
                "budget_frac": round(decision.budget_frac, 6),
                "est_tail_excursion": round(decision.est_tail_excursion, 6),
                "reasons": decision.reasons,
            },
        }
        self._atomic_write(TARGETS_FILE, payload)

        # The EA reads this. Header line carries the metadata as key=value
        # pairs; one "SYMBOL,weight" row per target.
        lines = [
            f"# nonce={nonce} ts={payload['ts_utc']} login={login} "
            f"verdict={payload['verdict']} scale={payload['scale']} "
            f"flatten={int(flatten)} n={len(payload['targets'])}"
        ]
        lines += [f"{t['symbol']},{t['weight']:.8f}" for t in payload["targets"]]
        self.dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.dir / TARGETS_CSV
        tmp = csv_path.with_suffix(".csv.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, csv_path)
        return nonce

    # --- reading ---------------------------------------------------------

    def _read(self, name: str) -> dict:
        path = self.dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def read_state(self) -> BrokerState:
        raw = self._read(STATE_FILE)
        if not raw:
            return BrokerState()
        return BrokerState(
            ts_utc=raw.get("ts_utc", ""),
            login=int(raw.get("login", 0) or 0),
            equity=float(raw.get("equity", 0.0) or 0.0),
            balance=float(raw.get("balance", 0.0) or 0.0),
            day_start_balance=float(raw.get("day_start_balance", 0.0) or 0.0),
            margin_free=float(raw.get("margin_free", 0.0) or 0.0),
            positions={p["symbol"]: float(p.get("net_lots", 0.0))
                       for p in raw.get("positions", []) if p.get("symbol")},
            last_actions=raw.get("last_actions", []),
            errors=raw.get("errors", []),
            nonce_seen=raw.get("nonce_seen", ""),
            connected=bool(raw.get("connected", False)),
        )

    def read_symbols(self) -> dict[str, dict]:
        """Broker symbol specs, as the EA sees them.

        Needed to reproduce the EA's sizing arithmetic on this side. Without
        it the reconciliation log can only report that live and simulated
        differ, not which step diverged.
        """
        return self._read(SYMBOLS_FILE).get("symbols", {})

    def expected_lots(
        self, weights: dict[str, float], equity: float
    ) -> tuple[dict[str, float], list[str]]:
        """Mirror of the EA's weight -> lots conversion, including rounding.

        Must stay identical to RoundToStep/LotsForWeight in TcsBridge.mq5:
        floor to the volume step (never oversize), and zero out anything below
        the minimum volume. Reproducing the rounding is the point - the common
        bug is a small target silently becoming no position at all.
        """
        specs = self.read_symbols()
        lots: dict[str, float] = {}
        notes: list[str] = []
        for sym, w in weights.items():
            spec = specs.get(sym)
            if not spec:
                notes.append(f"{sym}: no broker spec published yet")
                continue
            npl = float(spec.get("notional_per_lot", 0.0) or 0.0)
            if npl <= 0:
                notes.append(f"{sym}: notional_per_lot unavailable")
                continue
            raw = w * equity / npl
            step = float(spec.get("volume_step", 0.01) or 0.01)
            vmin = float(spec.get("volume_min", 0.0) or 0.0)
            vmax = float(spec.get("volume_max", 1e9) or 1e9)
            mag = abs(raw)
            r = int(mag / step + 1e-9) * step
            if r < vmin:
                if abs(w) > 1e-9:
                    notes.append(
                        f"{sym}: weight {w:+.5f} -> {raw:+.4f} lots, below the "
                        f"minimum {vmin} - will not be held"
                    )
                continue
            r = min(r, vmax)
            lots[sym] = -r if raw < 0 else r
        return lots, notes

    def volume_steps(self) -> dict[str, float]:
        """Per-symbol reconciliation tolerance: one volume step."""
        return {s: float(v.get("volume_step", 0.01) or 0.01)
                for s, v in self.read_symbols().items()}

    def heartbeat_age(self) -> float:
        return _age_seconds(self._read(HEARTBEAT_FILE).get("ts_utc", ""))

    def health(self) -> tuple[bool, list[str]]:
        """Is the terminal alive and reporting fresh, usable state?"""
        problems: list[str] = []
        hb = self.heartbeat_age()
        if hb == float("inf"):
            problems.append("no heartbeat file - is the EA attached and running?")
        elif hb > self.max_state_age_s:
            problems.append(f"heartbeat is {hb:.0f}s old (limit {self.max_state_age_s:.0f}s)")

        st = self.read_state()
        if not st.ts_utc:
            problems.append("no state file")
        elif st.age_seconds > self.max_state_age_s:
            problems.append(f"state is {st.age_seconds:.0f}s old")
        if st.ts_utc and not st.connected:
            problems.append("EA reports the terminal is not connected to the broker")
        if st.errors:
            problems += [f"EA error: {e}" for e in st.errors[:5]]
        return (not problems), problems

    def to_account_state(self):
        """Adapt the broker snapshot into the guard's input type."""
        from .guard import AccountState

        st = self.read_state()
        ok, _ = self.health()
        return AccountState(
            equity=st.equity,
            balance=st.balance,
            # Falls back to balance on the first run, before the EA has recorded
            # a day-open figure. Conservative: equal values mean full allowance.
            day_start_balance=st.day_start_balance or st.balance,
            open_positions=st.positions,
            age_seconds=st.age_seconds,
            connected=ok and st.connected,
        )
