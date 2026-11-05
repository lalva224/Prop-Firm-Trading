"""Detect prop-firm rule changes, and fail safe when they happen.

WHY THIS EXISTS
Every EV number in this project is computed against a specific set of rules.
If FTMO changes a profit target, a loss limit, the split, or introduces a
consistency rule, the model silently becomes wrong - and the documented
failure mode across the industry is exactly that: rules reinterpreted or
changed under an existing account.

So the rules are treated as a dependency with a version, not as a constant.

THE RESPONSE IS TO STOP, NOT TO ADAPT
There is no safe automatic adaptation to an unknown rule change. If the live
rules no longer match the encoded ones, the correct action is to stop
publishing targets and flatten - the same fail-flat path the guard already
uses for a stale feed. A human re-verifies, updates the encoded rules, and
re-runs the EV before trading resumes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parents[2] / "rules_archive"


@dataclass(frozen=True)
class RuleSet:
    """The rules every EV number in this project is computed against."""
    firm: str
    product: str
    phase_targets: tuple[float, ...]
    max_daily_loss: float
    max_total_loss: float
    drawdown_type: str            # "static" | "trailing" - type matters more than size
    min_trading_days: int
    max_days: int | None
    profit_split: float
    consistency_rule: float | None
    weekend_holding: bool
    news_restriction: bool
    verified_on: str              # ISO date a human last checked the live rules
    source_url: str

    def fingerprint(self) -> str:
        """Stable hash of the rules themselves, ignoring the verification date."""
        d = {k: v for k, v in asdict(self).items() if k != "verified_on"}
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def age_days(self) -> int:
        try:
            return (date.today() - date.fromisoformat(self.verified_on)).days
        except ValueError:
            return 10_000


# What the EV numbers in docs/ are computed against. Changing any value here
# invalidates every published figure until the harness is re-run.
FTMO_SWING_2STEP = RuleSet(
    firm="FTMO US (OANDA)",
    product="Swing 2-Step $100k",
    phase_targets=(0.10, 0.05),
    max_daily_loss=0.05,
    max_total_loss=0.10,
    drawdown_type="static",
    min_trading_days=4,
    max_days=None,
    profit_split=0.80,
    consistency_rule=None,
    weekend_holding=True,          # Swing allows it; Standard does not
    news_restriction=False,        # Swing has none; Standard has a 2-min window
    verified_on="2026-10-27",
    source_url="https://ftmo.oanda.com/",
)

# Fields whose change makes the model wrong rather than merely different.
CRITICAL = (
    "phase_targets", "max_daily_loss", "max_total_loss", "drawdown_type",
    "profit_split", "consistency_rule", "weekend_holding",
)


@dataclass
class RuleDiff:
    field: str
    encoded: object
    observed: object
    critical: bool

    def __str__(self) -> str:
        tag = "CRITICAL" if self.critical else "minor"
        return f"[{tag}] {self.field}: encoded={self.encoded!r} observed={self.observed!r}"


@dataclass
class WatchResult:
    ok: bool
    stale: bool
    age_days: int
    diffs: list[RuleDiff] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def must_halt(self) -> bool:
        """Any critical difference, or rules too old to trust, stops trading."""
        return any(d.critical for d in self.diffs) or self.stale

    def report(self) -> str:
        lines = [
            f"rules age     {self.age_days} days"
            + ("  *** STALE ***" if self.stale else ""),
            f"differences   {len(self.diffs)}",
        ]
        lines += [f"  {d}" for d in self.diffs]
        lines += [f"  note: {n}" for n in self.notes]
        lines.append(
            "VERDICT: HALT - re-verify the live rules before trading"
            if self.must_halt else "VERDICT: rules unchanged, safe to trade"
        )
        return "\n".join(lines)


def compare(encoded: RuleSet, observed: dict) -> list[RuleDiff]:
    """Compare encoded rules against values read from the live source."""
    out: list[RuleDiff] = []
    enc = asdict(encoded)
    for k, v in observed.items():
        if k not in enc or v is None:
            continue
        a, b = enc[k], v
        if isinstance(a, tuple):
            b = tuple(b)
        if isinstance(a, float) and isinstance(b, (int, float)):
            if abs(a - b) < 1e-9:
                continue
        elif a == b:
            continue
        out.append(RuleDiff(k, a, b, k in CRITICAL))
    return out


def check(
    encoded: RuleSet = FTMO_SWING_2STEP,
    observed: dict | None = None,
    max_age_days: int = 30,
) -> WatchResult:
    """Verify the encoded rules are current and unchanged."""
    age = encoded.age_days()
    stale = age > max_age_days
    diffs = compare(encoded, observed or {})
    notes = []
    if stale:
        notes.append(
            f"last human verification was {age} days ago (limit {max_age_days}); "
            f"re-check {encoded.source_url} and update verified_on"
        )
    if observed is None:
        notes.append("no live observation supplied - staleness checked only")
    return WatchResult(ok=not diffs and not stale, stale=stale,
                       age_days=age, diffs=diffs, notes=notes)


def archive(encoded: RuleSet, html: str | None = None) -> Path:
    """Snapshot the rules as of today, so a later change is provable.

    Industry failures were driven by retroactive reinterpretation; a dated,
    hashed copy is what turns 'they changed it' from a claim into a record.
    """
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    meta = ARCHIVE / f"{stamp}-{encoded.fingerprint()}.json"
    meta.write_text(json.dumps(
        {"captured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "fingerprint": encoded.fingerprint(),
         "rules": asdict(encoded),
         "page_sha256": hashlib.sha256(html.encode()).hexdigest() if html else None},
        indent=2))
    if html:
        (ARCHIVE / f"{stamp}-page.html").write_text(html)
    return meta


def parse_dashboard(text: str) -> dict:
    """Best-effort extraction of rule values from dashboard/objectives text.

    Deliberately conservative: a value only counts when the pattern is
    unambiguous. A missed field is a prompt to check manually; a wrongly
    parsed one would silently approve a changed rule.
    """
    out: dict = {}
    t = re.sub(r"\s+", " ", text)
    if m := re.search(r"Max\s*Daily\s*Loss[^0-9$]*\$?([\d,]+)", t, re.I):
        out["_daily_loss_cash"] = float(m.group(1).replace(",", ""))
    if m := re.search(r"Max\s*Loss[^0-9$]*\$?([\d,]+)", t, re.I):
        out["_max_loss_cash"] = float(m.group(1).replace(",", ""))
    if m := re.search(r"Profit\s*Target[^0-9$]*\$?([\d,]+)", t, re.I):
        out["_profit_target_cash"] = float(m.group(1).replace(",", ""))
    if m := re.search(r"[Mm]inimum\s*(\d+)\s*[Tt]rading\s*[Dd]ays", t):
        out["min_trading_days"] = int(m.group(1))
    if re.search(r"consistency", t, re.I):
        out["_mentions_consistency"] = True
    return out


def from_cash_amounts(obs: dict, account_size: float) -> dict:
    """Convert dashboard dollar figures into the fractions we encode."""
    out = {k: v for k, v in obs.items() if not k.startswith("_")}
    if (v := obs.get("_daily_loss_cash")) is not None:
        out["max_daily_loss"] = round(v / account_size, 6)
    if (v := obs.get("_max_loss_cash")) is not None:
        out["max_total_loss"] = round(v / account_size, 6)
    return out
