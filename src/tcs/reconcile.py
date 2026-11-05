"""Simulated-versus-live reconciliation log.

This is the actual deliverable of demo testing. Passing a demo challenge proves
nothing; proving the live book matches the simulated book proves the bot works.

Every bug worth catching before real money shows up here as a divergence:
wrong symbol mapping, lot rounding that silently zeroes a small position,
a rebalance firing in the wrong timezone, a minimum-volume floor the simulator
does not model, or the guard scaling the book without the strategy knowing.

Appends one JSONL row per run, so the history is greppable and never rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEFAULT_LOG = Path("out/reconcile.jsonl")


@dataclass
class Divergence:
    symbol: str
    simulated: float
    live: float
    diff: float
    reason: str


@dataclass
class ReconcileRow:
    ts_utc: str
    nonce: str
    equity: float
    day_start_balance: float
    headroom: float
    guard_verdict: str
    guard_scale: float
    sim_gross: float
    live_gross: float
    max_abs_diff: float
    n_divergent: int
    divergences: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compare(
    simulated_lots: dict[str, float],
    live_lots: dict[str, float],
    tolerance: dict[str, float] | float = 0.01,
) -> list[Divergence]:
    """Per-symbol comparison in LOTS, the unit the broker actually holds.

    Comparing weights would hide the bug class that matters most - rounding to
    the broker's volume step. Tolerance may be a single number or per-symbol,
    because the acceptable error is one volume step for that instrument.
    """
    out: list[Divergence] = []
    for sym in sorted(set(simulated_lots) | set(live_lots)):
        sim = float(simulated_lots.get(sym, 0.0))
        live = float(live_lots.get(sym, 0.0))
        tol = tolerance.get(sym, 0.01) if isinstance(tolerance, dict) else tolerance
        diff = live - sim
        if abs(diff) <= tol + 1e-9:   # guard against float dust
            continue
        if sym not in live_lots:
            reason = "missing live - order never placed or was rejected"
        elif sym not in simulated_lots:
            reason = "unexpected live position - stale or manual"
        elif abs(live) < 1e-9:
            reason = "rounded to zero - below the broker's minimum volume"
        elif sim * live < 0:
            reason = "WRONG SIGN - live position is opposite the target"
        else:
            reason = "size mismatch"
        out.append(Divergence(sym, sim, live, diff, reason))
    return out


def log_run(
    row: ReconcileRow,
    path: str | Path = DEFAULT_LOG,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(row.__dict__, sort_keys=True) + "\n")
    return p


def build_row(
    nonce: str,
    simulated_lots: dict[str, float],
    live_lots: dict[str, float],
    equity: float,
    day_start_balance: float,
    headroom: float,
    guard_verdict: str,
    guard_scale: float,
    tolerance: dict[str, float] | float = 0.01,
    notes: list[str] | None = None,
) -> ReconcileRow:
    divs = compare(simulated_lots, live_lots, tolerance)
    return ReconcileRow(
        ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        nonce=nonce,
        equity=round(equity, 2),
        day_start_balance=round(day_start_balance, 2),
        headroom=round(headroom, 2),
        guard_verdict=guard_verdict,
        guard_scale=round(guard_scale, 6),
        sim_gross=round(sum(abs(v) for v in simulated_lots.values()), 4),
        live_gross=round(sum(abs(v) for v in live_lots.values()), 4),
        max_abs_diff=round(max((abs(d.diff) for d in divs), default=0.0), 4),
        n_divergent=len(divs),
        divergences=[d.__dict__ for d in divs],
        notes=notes or [],
    )


def load_log(path: str | Path = DEFAULT_LOG) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return pd.DataFrame(rows)


def summarise(path: str | Path = DEFAULT_LOG) -> str:
    df = load_log(path)
    if df.empty:
        return "no reconciliation runs logged yet"
    clean = int((df.n_divergent == 0).sum())
    lines = [
        f"runs logged        {len(df)}",
        f"clean runs         {clean} ({clean / len(df):.0%})",
        f"worst lot diff     {df.max_abs_diff.max():.4f}",
        f"verdicts           {df.guard_verdict.value_counts().to_dict()}",
    ]
    bad = df[df.n_divergent > 0]
    if not bad.empty:
        reasons: dict[str, int] = {}
        for divs in bad.divergences:
            for d in divs:
                reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        lines.append("divergence reasons:")
        lines += [f"  {v:>4}  {k}" for k, v in
                  sorted(reasons.items(), key=lambda x: -x[1])]
        wrong = reasons.get("WRONG SIGN - live position is opposite the target", 0)
        if wrong:
            lines.append(f"  *** {wrong} WRONG-SIGN events - stop and fix before live ***")
    return "\n".join(lines)
