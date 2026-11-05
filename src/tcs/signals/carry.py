"""Carry.

Carry is the second-strongest documented cross-asset premium after trend
(Koijen/Moskowitz/Pedersen/Vrugt, 2018): hold the high-yielding asset, short
the low-yielding one, and collect the yield differential.

IMPLEMENTED
  funding_carry_signal - crypto perpetual funding carry. Free data from the
  Binance API, mechanically positive expected carry, and the most accessible
  real carry trade available to a retail account.

NOT IMPLEMENTED (deliberately stubbed, not faked)
  futures_term_carry - requires two dated contracts per market to compute the
  term-structure slope. yfinance only exposes a single continuous front-month
  series, so this cannot be computed from the free data source. Wire it up when
  a real contract-level feed (Databento) is connected. Returning a fabricated
  proxy here would silently corrupt every backtest that used it.
"""

from __future__ import annotations

import pandas as pd

FUNDING_INTERVALS_PER_YEAR = 365 * 3  # paid every 8 hours


def annualize_funding(funding: pd.Series) -> pd.Series:
    """Per-interval funding rate -> annualised rate."""
    return funding * FUNDING_INTERVALS_PER_YEAR


def funding_carry_signal(
    funding: pd.DataFrame,
    smooth_days: int = 7,
    scale: float = 0.25,
) -> pd.DataFrame:
    """Daily carry signal from perp funding rates.

    Positive funding means longs pay shorts, so the carry-earning position is
    SHORT the perp -> signal is the negative of smoothed annualised funding.

    `scale` maps annualised funding to a unit signal: at the default, 25%
    annualised funding produces a full-strength (1.0) signal. Clipped to
    [-1, 1] so a funding spike cannot dominate the portfolio.

    Args:
        funding: DataFrame indexed by timestamp, one column per symbol, values
            are per-interval funding rates.
    """
    daily = funding.resample("1D").sum()
    annualised = annualize_funding(daily) / 3  # daily sum already covers 3 intervals
    smoothed = annualised.rolling(smooth_days, min_periods=1).mean()
    return (-smoothed / scale).clip(-1.0, 1.0)


def futures_term_carry(
    near: pd.DataFrame,
    far: pd.DataFrame,
    days_between: pd.DataFrame | float,
    scale: float = 0.10,
) -> pd.DataFrame:
    """Annualised term-structure carry from two dated contracts.

    carry = (near / far - 1) * (365 / days_between)

    Backwardation (near > far) gives positive carry -> long signal.
    Requires a contract-level feed; see module docstring.
    """
    raw = (near / far - 1.0) * (365.0 / days_between)
    return (raw / scale).clip(-1.0, 1.0)
