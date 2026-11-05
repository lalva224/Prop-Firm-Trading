"""Time-series momentum (trend following).

The single most documented effect in finance: Moskowitz/Ooi/Pedersen (2012),
and AQR's "A Century of Evidence on Trend-Following Investing" which tests it
back to 1880. Edge source is behavioural + risk premium, not speed - which is
exactly why it has not been arbitraged away by faster participants.

Two implementations, both volatility-normalised so signals are comparable
across a bond future and a natural gas future:

  vol_adjusted_momentum - lookback return / expected vol over that lookback
  ewmac                 - EWMA crossover / vol, the Carver-style formulation

Both are squashed with tanh so a huge move cannot dominate the book.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_LOOKBACKS = (21, 63, 252)


def ewma_daily_vol(returns: pd.DataFrame, halflife: int = 32) -> pd.DataFrame:
    """Daily (not annualised) EWMA volatility, floored to avoid blowups."""
    vol = returns.ewm(halflife=halflife, min_periods=halflife).std()
    # Floor at a small fraction of each asset's own long-run vol.
    floor = returns.expanding(min_periods=252).std() * 0.1
    return vol.clip(lower=floor)


def vol_adjusted_momentum(
    prices: pd.DataFrame,
    lookback: int,
    vol_halflife: int = 32,
    clip: float = 2.0,
) -> pd.DataFrame:
    """Return over `lookback` days, scaled by expected vol over that horizon."""
    rets = prices.pct_change()
    raw = prices / prices.shift(lookback) - 1.0
    expected = ewma_daily_vol(rets, vol_halflife) * np.sqrt(lookback)
    score = (raw / expected).clip(-clip, clip)
    return np.tanh(score)


def ewmac_signal(
    prices: pd.DataFrame,
    fast: int,
    slow: int,
    vol_halflife: int = 32,
    clip: float = 2.0,
) -> pd.DataFrame:
    """EWMA crossover normalised by price volatility."""
    rets = prices.pct_change()
    fast_ma = prices.ewm(span=fast, min_periods=fast).mean()
    slow_ma = prices.ewm(span=slow, min_periods=slow).mean()
    # Crossover expressed in return space, scaled by vol over the slow horizon.
    raw = (fast_ma - slow_ma) / prices
    expected = ewma_daily_vol(rets, vol_halflife) * np.sqrt(slow)
    score = (raw / expected).clip(-clip, clip)
    return np.tanh(score)


def ewmac_blend(
    prices: pd.DataFrame,
    pairs: tuple[tuple[int, int], ...] = ((16, 64), (32, 128), (64, 256)),
    vol_halflife: int = 32,
    clip: float = 2.0,
    smooth: int = 0,
) -> pd.DataFrame:
    """Blend of EWMA crossovers. Smoother than lookback returns because no
    single past price rolls out of the window discontinuously."""
    parts = [ewmac_signal(prices, f, s, vol_halflife, clip) for f, s in pairs]
    stacked = pd.concat(parts, axis=0, keys=range(len(parts)))
    out = stacked.groupby(level=1).mean().reindex(prices.index)
    return out.ewm(span=smooth, min_periods=1).mean() if smooth > 0 else out


def trend_signal(
    prices: pd.DataFrame,
    lookbacks: tuple[int, ...] = DEFAULT_LOOKBACKS,
    vol_halflife: int = 32,
    clip: float = 2.0,
    smooth: int = 0,
) -> pd.DataFrame:
    """Blend of multiple lookbacks - the standard CTA construction.

    Averaging horizons is not a free lunch but it materially reduces the
    sensitivity of results to one arbitrary parameter choice, which is the
    main way trend backtests get overfitted.
    """
    parts = [
        vol_adjusted_momentum(prices, lb, vol_halflife, clip) for lb in lookbacks
    ]
    stacked = pd.concat(parts, axis=0, keys=range(len(parts)))
    out = stacked.groupby(level=1).mean().reindex(prices.index)
    # Smoothing a forecast is the cheapest turnover reduction available:
    # it costs almost no signal but cuts day-to-day churn substantially.
    return out.ewm(span=smooth, min_periods=1).mean() if smooth > 0 else out
