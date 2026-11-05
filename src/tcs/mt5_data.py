"""Load broker-exported MT5 history into the research pipeline.

Produced by mql5/DumpHistory.mq5. The point of using broker data rather than
yfinance proxies is that it removes two confounds at once:

  1. ROLL ARTEFACTS. Every commodity series used so far was yfinance
     front-month continuous futures, which are NOT back-adjusted - each
     contract roll injects a fake gap into the signal and the P&L. Broker CFD
     series are continuous by construction. This is the single confound that
     has been blocking the project, and the export removes it for free.
  2. PROXY MISMATCH. ^GSPC is a price index with no dividend adjustment and
     EURUSD=X is spot with no carry; the broker's US500 and EURUSD are the
     actual tradeable instruments, with the actual spreads.

What it costs: history depth is whatever the broker holds, which may be far
less than 21 years. `coverage()` reports exactly how much, which is the number
that decides whether this replaces a paid feed or merely supplements it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def read_index(directory: str | Path) -> pd.DataFrame:
    """Per-symbol bar counts and date ranges from the exporter's index file."""
    p = Path(directory).expanduser() / "_index.json"
    if not p.exists():
        return pd.DataFrame()
    text = p.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # MQL5 writes SYMBOL_PATH ("Forex\\Majors") without escaping the
        # backslash. Double any backslash that is not already a valid JSON
        # escape, rather than discarding an otherwise good export.
        import re
        raw = json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text))
    df = pd.DataFrame(raw.get("symbols", []))
    if df.empty:
        return df
    for c in ("first", "last"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["years"] = (df["last"] - df["first"]).dt.days / 365.25
    return df.sort_values("bars", ascending=False).reset_index(drop=True)


def load_ohlc(
    directory: str | Path,
    symbols: list[str] | None = None,
    min_bars: int = 500,
) -> dict[str, pd.DataFrame]:
    """OHLC frames keyed open/high/low/close, columns=symbols.

    Deliberately mirrors `data.load_ohlc` so every downstream component -
    signals, backtest, the FTMO harness, the guard - works unchanged on broker
    data with no code edits.
    """
    d = Path(directory).expanduser()
    files = sorted(d.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSVs in {d} - run DumpHistory.mq5 first")

    frames: dict[str, pd.DataFrame] = {}
    for f in files:
        sym = f.stem
        if symbols and sym not in symbols:
            continue
        df = pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if len(df) < min_bars:
            log.warning("skipping %s: only %d bars", sym, len(df))
            continue
        frames[sym] = df

    if not frames:
        raise ValueError("no symbol met the min_bars threshold")

    out: dict[str, pd.DataFrame] = {}
    for field in ("open", "high", "low", "close"):
        out[field] = pd.DataFrame(
            {s: df[field] for s, df in frames.items()}
        ).sort_index().ffill()
    # Spread in bps, measured from the broker rather than assumed.
    #
    # MqlRates.spread is in POINTS, not price units, so it must be multiplied
    # by the symbol's point size before being compared to price. Getting this
    # wrong inflates the figure by orders of magnitude - for a 5-digit FX pair
    # the error is 100,000x - which would make every instrument look untradeable.
    points = _point_sizes(d)
    spreads = {}
    for s, df in frames.items():
        pt = points.get(s)
        if pt is None or pt <= 0:
            log.warning("no point size for %s; spread left as NaN rather than "
                        "mis-scaled", s)
            spreads[s] = pd.Series(float("nan"), index=df.index)
            continue
        spreads[s] = df["spread"] * pt / df["close"].where(df["close"] > 0) * 1e4
    out["spread_bps"] = pd.DataFrame(spreads).sort_index()
    return out


def _point_sizes(directory: Path) -> dict[str, float]:
    """Point size per symbol, from the export index, with a digits fallback."""
    idx = read_index(directory)
    if idx.empty:
        return {}
    out: dict[str, float] = {}
    for _, r in idx.iterrows():
        pt = r.get("point")
        if pt is None or not float(pt or 0) > 0:
            digits = r.get("digits")
            pt = 10.0 ** -int(digits) if digits is not None and digits == digits else None
        if pt:
            out[str(r["symbol"])] = float(pt)
    return out


def coverage(directory: str | Path) -> str:
    """Human-readable answer to 'is this enough history to trust?'"""
    df = read_index(directory)
    if df.empty:
        return "no _index.json found - run DumpHistory.mq5 first"
    lines = [
        f"symbols exported   {len(df)}",
        f"median history     {df['years'].median():.1f} years",
        f"longest            {df['years'].max():.1f} years ({df.loc[df['years'].idxmax(), 'symbol']})",
        f"shortest           {df['years'].min():.1f} years ({df.loc[df['years'].idxmin(), 'symbol']})",
        f"with 10y+          {(df['years'] >= 10).sum()}",
        f"with 15y+          {(df['years'] >= 15).sum()}",
        "",
        "A trend system needs 10y+ to span more than one regime. Fewer than ~15",
        "symbols at 10y+ means the broker export supplements a paid feed rather",
        "than replacing it.",
    ]
    return "\n".join(lines)


def classify(directory: str | Path) -> pd.DataFrame:
    """Group symbols by the broker's own instrument path.

    SYMBOL_PATH is how the broker organises its tree (e.g. "Forex\\Majors",
    "Indices\\US", "Commodities\\Energy"). It is the most reliable way to find
    out whether any BOND or RATE instrument exists - the sleeve that measured
    Sharpe 0.87 and that every published list says FTMO does not offer.
    """
    df = read_index(directory)
    if df.empty or "path" not in df:
        return df
    df["group"] = df["path"].fillna("").str.split("\\").str[0]
    df["subgroup"] = df["path"].fillna("").str.split("\\").str[1].fillna("")
    return df[["symbol", "group", "subgroup", "bars", "years", "description"]]
