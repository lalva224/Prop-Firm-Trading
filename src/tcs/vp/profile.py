"""Volume profile construction: POC, Value Area, VAH, VAL.

Two builders:

  profile_from_trades - exact. Each trade's volume lands in one price bin.
      Use when tick data is available.
  build_profile       - approximate. Distributes each bar's volume uniformly
      across the bins its [low, high] range spans. At 1-minute granularity the
      error is small, and it lets us research on free kline data. The POC it
      produces is close to, but not identical to, the tick-exact POC.

The Value Area algorithm is the standard one: start at the POC and expand
outward, each step taking whichever adjacent side holds more volume, until the
accumulated volume reaches `value_area_pct` of the session total.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VolumeProfile:
    poc: float
    vah: float
    val: float
    bins: pd.Series  # index = price bin, value = volume
    total_volume: float

    @property
    def va_width(self) -> float:
        return self.vah - self.val

    def __repr__(self) -> str:
        return (
            f"VolumeProfile(poc={self.poc:.2f}, val={self.val:.2f}, "
            f"vah={self.vah:.2f}, vol={self.total_volume:,.0f})"
        )


def _value_area(bins: pd.Series, value_area_pct: float) -> tuple[float, float, float]:
    """Expand outward from the POC until `value_area_pct` of volume is enclosed."""
    if bins.empty or bins.sum() <= 0:
        return (np.nan, np.nan, np.nan)

    prices = bins.index.to_numpy(dtype=float)
    vols = bins.to_numpy(dtype=float)
    target = vols.sum() * value_area_pct

    poc_i = int(np.argmax(vols))
    lo = hi = poc_i
    acc = vols[poc_i]

    while acc < target and (lo > 0 or hi < len(vols) - 1):
        below = vols[lo - 1] if lo > 0 else -1.0
        above = vols[hi + 1] if hi < len(vols) - 1 else -1.0
        if above >= below:
            hi += 1
            acc += vols[hi]
        else:
            lo -= 1
            acc += vols[lo]

    return (float(prices[poc_i]), float(prices[hi]), float(prices[lo]))


def _bin_prices(prices: np.ndarray, tick_size: float) -> np.ndarray:
    return np.round(prices / tick_size) * tick_size


def profile_from_trades(
    trades: pd.DataFrame,
    tick_size: float,
    value_area_pct: float = 0.70,
    price_col: str = "price",
    size_col: str = "size",
) -> VolumeProfile:
    """Exact profile from tick data."""
    if trades.empty:
        return VolumeProfile(np.nan, np.nan, np.nan, pd.Series(dtype=float), 0.0)
    binned = _bin_prices(trades[price_col].to_numpy(dtype=float), tick_size)
    bins = (
        pd.Series(trades[size_col].to_numpy(dtype=float), index=binned)
        .groupby(level=0)
        .sum()
        .sort_index()
    )
    poc, vah, val = _value_area(bins, value_area_pct)
    return VolumeProfile(poc, vah, val, bins, float(bins.sum()))


def build_profile(
    bars: pd.DataFrame,
    tick_size: float,
    value_area_pct: float = 0.70,
    max_bins_per_bar: int = 200,
) -> VolumeProfile:
    """Approximate profile from OHLCV bars.

    Each bar's volume is spread evenly over the price bins between its low and
    high. Bars whose range spans more than `max_bins_per_bar` bins are clipped
    so one spike bar cannot smear volume across the entire session.

    Fully vectorised: a per-bar Python loop costs minutes on a multi-year
    1-minute dataset, which makes parameter sweeps impractical.
    """
    if bars.empty:
        return VolumeProfile(np.nan, np.nan, np.nan, pd.Series(dtype=float), 0.0)

    lows = _bin_prices(bars["low"].to_numpy(dtype=float), tick_size)
    highs = _bin_prices(bars["high"].to_numpy(dtype=float), tick_size)
    vols = bars["volume"].to_numpy(dtype=float)

    ok = np.isfinite(lows) & np.isfinite(highs) & np.isfinite(vols) & (vols > 0)
    lows, highs, vols = lows[ok], highs[ok], vols[ok]
    if lows.size == 0:
        return VolumeProfile(np.nan, np.nan, np.nan, pd.Series(dtype=float), 0.0)

    n = np.rint((highs - lows) / tick_size).astype(np.int64) + 1
    n = np.clip(n, 1, max_bins_per_bar)

    # Expand every bar into its n bins without a Python loop.
    total = int(n.sum())
    starts = np.zeros(n.size, dtype=np.int64)
    np.cumsum(n[:-1], out=starts[1:])
    offsets = np.arange(total, dtype=np.int64) - np.repeat(starts, n)

    step = np.where(n > 1, (highs - lows) / np.maximum(n - 1, 1), 0.0)
    prices = np.repeat(lows, n) + offsets * np.repeat(step, n)
    shares = np.repeat(vols / n, n)

    keys = np.rint(prices / tick_size).astype(np.int64)
    base = keys.min()
    agg = np.bincount(keys - base, weights=shares)
    nz = np.flatnonzero(agg)

    bins = pd.Series(agg[nz], index=(nz + base) * tick_size)
    poc, vah, val = _value_area(bins, value_area_pct)
    return VolumeProfile(poc, vah, val, bins, float(bins.sum()))


def session_profiles(
    bars: pd.DataFrame,
    tick_size: float,
    value_area_pct: float = 0.70,
    session: str = "1D",
) -> pd.DataFrame:
    """One profile per session. Returns poc/vah/val/volume indexed by session."""
    rows = []
    for ts, grp in bars.groupby(pd.Grouper(freq=session)):
        if grp.empty:
            continue
        p = build_profile(grp, tick_size, value_area_pct)
        rows.append(
            {
                "session": ts,
                "poc": p.poc,
                "vah": p.vah,
                "val": p.val,
                "volume": p.total_volume,
                "high": float(grp["high"].max()),
                "low": float(grp["low"].min()),
                "close": float(grp["close"].iloc[-1]),
            }
        )
    return pd.DataFrame(rows).set_index("session") if rows else pd.DataFrame()
