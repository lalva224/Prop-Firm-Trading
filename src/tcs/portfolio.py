"""Position sizing and portfolio-level volatility targeting.

The sizing layer matters more than the signal layer. Risk parity across
instruments stops one volatile market dominating, and portfolio vol targeting
keeps realised risk roughly constant as correlations shift - which is what
turns a signal with positive expectancy into a tolerable equity curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ewma_vol(
    returns: pd.DataFrame, halflife: int = 32, annualize: bool = True
) -> pd.DataFrame:
    vol = returns.ewm(halflife=halflife, min_periods=halflife).std()
    floor = returns.expanding(min_periods=252).std() * 0.1
    vol = vol.clip(lower=floor)
    return vol * np.sqrt(252) if annualize else vol


def risk_parity_positions(
    signals: pd.DataFrame,
    vols: pd.DataFrame,
    gross_cap: float = 1.0,
) -> pd.DataFrame:
    """Signal-weighted inverse-vol positions, normalised to a gross exposure cap.

    Each position is signal / vol, so every instrument contributes comparable
    risk at full signal strength. Rows are then scaled so total absolute weight
    equals `gross_cap`, making the result independent of universe size.
    """
    raw = signals / vols
    raw = raw.replace([np.inf, -np.inf], np.nan)
    gross = raw.abs().sum(axis=1)
    weights = raw.div(gross.where(gross > 0), axis=0) * gross_cap
    return weights.fillna(0.0)


def vol_target_scalar(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    target_ann_vol: float = 0.12,
    lookback: int = 252,
    min_periods: int = 126,
    max_leverage: float = 3.0,
) -> pd.Series:
    """Scalar per date that scales the book toward a constant realised vol.

    Computed from the TRAILING realised vol of the unscaled strategy, which
    avoids the circularity of targeting your own scaled output. Uses only
    information available up to and including each date; the caller is
    responsible for the execution lag.
    """
    unscaled_pnl = (weights.shift(1) * returns).sum(axis=1)
    realised = unscaled_pnl.rolling(lookback, min_periods=min_periods).std() * np.sqrt(252)
    scalar = (target_ann_vol / realised.where(realised > 0)).clip(upper=max_leverage)
    return scalar.fillna(0.0)


def buffer_positions(target: pd.DataFrame, buffer_frac: float = 0.10) -> pd.DataFrame:
    """No-trade band around the target position.

    Trend signals drift continuously, so trading to the exact optimum every day
    generates enormous turnover for negligible risk benefit - and turnover is
    paid in real money. Standard fix: only trade an instrument when its target
    has moved more than `buffer_frac` of the typical position size, otherwise
    hold what you have.

    Causal by construction: row i depends only on row i and the running state.
    """
    tgt = np.nan_to_num(target.to_numpy(dtype=float), nan=0.0)
    typical = np.abs(tgt).mean(axis=1)
    band = buffer_frac * typical

    held = np.zeros_like(tgt)
    current = np.zeros(tgt.shape[1])
    for i in range(tgt.shape[0]):
        if np.isfinite(band[i]) and band[i] > 0:
            move = np.abs(tgt[i] - current) > band[i]
            current = np.where(move, tgt[i], current)
        held[i] = current
    return pd.DataFrame(held, index=target.index, columns=target.columns)
