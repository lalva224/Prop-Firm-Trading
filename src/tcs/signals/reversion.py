"""Short-term mean reversion.

Deliberately constructed to mirror the trend signals exactly - same vol
normalisation, same clip, same tanh squash - so the two are on a common scale
and can be blended without one silently dominating.

Caveats that belong next to the code, not buried in a doc:
  - This is a MUCH weaker effect than trend in liquid futures, and it has
    decayed since the 2000s.
  - Its skew is negative: many small wins, occasional large loss. You are
    effectively selling liquidity, so you get paid like an insurer.
  - Turnover is higher than trend, so costs matter more.
  - It fails precisely when trend succeeds. That is the diversification
    benefit and the tail risk, simultaneously.
It belongs as a minority risk allocation, never as half the book.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .trend import ewma_daily_vol


def zscore_reversion(
    prices: pd.DataFrame,
    lookback: int = 10,
    vol_halflife: int = 32,
    clip: float = 2.0,
    smooth: int = 0,
) -> pd.DataFrame:
    """Vol-normalised deviation from own moving average, inverted.

    Stretched above the mean -> negative (short) signal, and vice versa.
    """
    ma = prices.rolling(lookback, min_periods=lookback).mean()
    deviation = prices / ma - 1.0
    expected = ewma_daily_vol(prices.pct_change(), vol_halflife) * np.sqrt(lookback)
    score = (-deviation / expected).clip(-clip, clip)
    out = np.tanh(score)
    return out.ewm(span=smooth, min_periods=1).mean() if smooth > 0 else out


def reversion_blend(
    prices: pd.DataFrame,
    lookbacks: tuple[int, ...] = (5, 10, 20),
    vol_halflife: int = 32,
    clip: float = 2.0,
    smooth: int = 0,
) -> pd.DataFrame:
    """Average of several reversion horizons, mirroring the trend blend."""
    parts = [
        zscore_reversion(prices, lb, vol_halflife, clip) for lb in lookbacks
    ]
    stacked = pd.concat(parts, axis=0, keys=range(len(parts)))
    out = stacked.groupby(level=1).mean().reindex(prices.index)
    return out.ewm(span=smooth, min_periods=1).mean() if smooth > 0 else out
