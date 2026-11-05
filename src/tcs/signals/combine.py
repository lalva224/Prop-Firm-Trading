"""Regime-weighted combination of the trend and reversion sleeves."""

from __future__ import annotations

from typing import Callable

import pandas as pd

from .regime import trendiness
from .reversion import reversion_blend
from .trend import ewmac_blend

SignalFn = Callable[[pd.DataFrame], pd.DataFrame]


def regime_blended_signal(
    prices: pd.DataFrame,
    trend_fn: SignalFn | None = None,
    reversion_fn: SignalFn | None = None,
    reversion_risk_share: float = 0.30,
    smooth: int = 10,
    **regime_kwargs,
) -> pd.DataFrame:
    """signal = w * trend + (1 - w) * reversion, w = per-instrument trendiness.

    `reversion_risk_share` caps how much of the book the reversion sleeve can
    ever claim. At 0.30, a fully mean-reverting market still allocates 70% of
    that instrument's signal to trend - deliberate, because reversion is the
    weaker and more negatively-skewed of the two effects.
    """
    trend_fn = trend_fn or (lambda p: ewmac_blend(p, smooth=smooth))
    reversion_fn = reversion_fn or (lambda p: reversion_blend(p, smooth=smooth))

    t_sig = trend_fn(prices)
    r_sig = reversion_fn(prices)
    w = trendiness(prices, **regime_kwargs)

    # Rescale so (1 - w) tops out at reversion_risk_share rather than at 1.0.
    rev_weight = (1.0 - w) * reversion_risk_share
    trend_weight = 1.0 - rev_weight
    return trend_weight * t_sig + rev_weight * r_sig


def gated_signal(
    prices: pd.DataFrame,
    max_trendiness: float = 0.30,
    min_trendiness: float = 0.30,
    smooth: int = 10,
    use_trend: bool = True,
    use_reversion: bool = True,
    **regime_kwargs,
) -> pd.DataFrame:
    """Hard regime gate: reversion only in chop, trend only when trending.

    Unlike regime_blended_signal (which always holds both sleeves, weighted),
    this goes FLAT in the regime where a sleeve loses money. Decomposing PnL by
    trendiness quintile showed both effects are strongly conditional and
    monotonic - reversion earns in the most lateral quintile and loses in the
    most trending one, and trend does the reverse. Holding a sleeve through its
    bad regime is what made the unconditional reversion test look edgeless.

    `trendiness` is already a rolling percentile in [0, 1], so a fixed
    threshold is well-defined and causal - no full-sample quantile needed.
    """
    w = trendiness(prices, **regime_kwargs)
    out = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    if use_reversion:
        out = out + reversion_blend(prices, smooth=smooth).where(w < max_trendiness, 0.0)
    if use_trend:
        out = out + ewmac_blend(prices, smooth=smooth).where(w >= min_trendiness, 0.0)
    return out
