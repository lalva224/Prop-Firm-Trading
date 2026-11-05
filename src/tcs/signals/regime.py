"""Regime measurement: how trending vs how mean-reverting is each market, now.

Trend following loses in chop; mean reversion loses in trends. The two are
complementary, but only if you can tell which state you are in - and the naive
approach (a binary classifier) whipsaws at the boundary, adding a cliff edge
exactly where confidence is lowest.

So we produce a CONTINUOUS trendiness score in [0, 1], per instrument per day,
and blend the two sleeves by it. Gold can be trending while bonds chop.

Two measures, both rank-normalised against each instrument's own history
because their raw levels are not comparable across assets or vol regimes:

  efficiency_ratio - Kaufman. |net move| / sum|daily moves|. 1.0 = perfectly
      directional, ~0 = pure chop. The direct expression of the idea.
  variance_ratio   - Lo & MacKinlay. Var(k-day) / (k * Var(1-day)). Above 1
      implies positive autocorrelation (trending), below 1 implies reversion.
      The statistically grounded cousin of the Hurst exponent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RANK_WINDOW = 504  # ~2y of self-history for normalisation


def efficiency_ratio(prices: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Kaufman Efficiency Ratio in [0, 1]. High = directional, low = chop."""
    net = (prices - prices.shift(window)).abs()
    path = prices.diff().abs().rolling(window).sum()
    return (net / path.where(path > 0)).clip(0.0, 1.0)


def variance_ratio(
    prices: pd.DataFrame, k: int = 5, window: int = 120
) -> pd.DataFrame:
    """Lo-MacKinlay variance ratio. >1 trending, <1 mean-reverting, 1 = random."""
    logp = np.log(prices.where(prices > 0))
    v1 = logp.diff().rolling(window).var()
    vk = logp.diff(k).rolling(window).var()
    return vk / (k * v1.where(v1 > 0))


def _rolling_pct_rank(df: pd.DataFrame, window: int = RANK_WINDOW) -> pd.DataFrame:
    """Percentile rank of each value against the instrument's own recent history.

    Self-normalising and causal: the rank at date t uses only data up to t.
    """
    return df.rolling(window, min_periods=window // 4).rank(pct=True)


def trendiness(
    prices: pd.DataFrame,
    er_window: int = 20,
    vr_k: int = 5,
    vr_window: int = 120,
    rank_window: int = RANK_WINDOW,
    er_weight: float = 0.5,
) -> pd.DataFrame:
    """Continuous trendiness in [0, 1]; 0.5 where undetermined or warming up.

    Defaults to 0.5 (an even split between sleeves) rather than to 0 or 1, so
    an unmeasurable regime does not silently become a directional bet.
    """
    er_rank = _rolling_pct_rank(efficiency_ratio(prices, er_window), rank_window)
    vr_rank = _rolling_pct_rank(
        variance_ratio(prices, vr_k, vr_window), rank_window
    )
    blended = er_weight * er_rank + (1.0 - er_weight) * vr_rank
    return blended.clip(0.0, 1.0).fillna(0.5)
