"""Performance statistics.

Deliberately includes turnover and a deflated-Sharpe warning: a Sharpe ratio
quoted without the number of variants tried to obtain it is not evidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cagr(net_returns: pd.Series) -> float:
    curve = (1.0 + net_returns.fillna(0.0)).cumprod()
    years = len(net_returns) / TRADING_DAYS
    if years <= 0 or curve.iloc[-1] <= 0:
        return float("nan")
    return curve.iloc[-1] ** (1.0 / years) - 1.0


def ann_vol(net_returns: pd.Series) -> float:
    return float(net_returns.std() * np.sqrt(TRADING_DAYS))


def sharpe(net_returns: pd.Series, rf_ann: float = 0.0) -> float:
    excess = net_returns - rf_ann / TRADING_DAYS
    sd = excess.std()
    if not sd or np.isnan(sd):
        return float("nan")
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(net_returns: pd.Series) -> float:
    downside = net_returns[net_returns < 0].std()
    if not downside or np.isnan(downside):
        return float("nan")
    return float(net_returns.mean() / downside * np.sqrt(TRADING_DAYS))


def max_drawdown(net_returns: pd.Series) -> float:
    curve = (1.0 + net_returns.fillna(0.0)).cumprod()
    return float((curve / curve.cummax() - 1.0).min())


def calmar(net_returns: pd.Series) -> float:
    mdd = max_drawdown(net_returns)
    return float("nan") if mdd == 0 else cagr(net_returns) / abs(mdd)


def deflated_sharpe_hint(observed_sharpe: float, n_trials: int, n_obs: int) -> float:
    """Rough haircut for multiple testing (Bailey & Lopez de Prado, simplified).

    Expected maximum Sharpe from `n_trials` independent random strategies with
    zero true edge. If your observed Sharpe is not comfortably above this, you
    have found nothing. Honest use requires counting EVERY variant you tried.
    """
    if n_trials < 2 or n_obs < 2:
        return 0.0
    euler = 0.5772156649
    expected_max_z = (1 - euler) * np.sqrt(2 * np.log(n_trials)) + euler * np.sqrt(
        2 * np.log(n_trials)
    ) / (2 * np.sqrt(2 * np.log(n_trials)))
    return float(expected_max_z / np.sqrt(n_obs) * np.sqrt(TRADING_DAYS))


def summary(net_returns: pd.Series, turnover: pd.Series | None = None) -> dict:
    out = {
        "cagr": cagr(net_returns),
        "ann_vol": ann_vol(net_returns),
        "sharpe": sharpe(net_returns),
        "sortino": sortino(net_returns),
        "max_dd": max_drawdown(net_returns),
        "calmar": calmar(net_returns),
        "skew": float(net_returns.skew()),
        "hit_rate": float((net_returns > 0).mean()),
        "n_days": int(net_returns.notna().sum()),
    }
    if turnover is not None:
        out["ann_turnover"] = float(turnover.mean() * TRADING_DAYS)
    return out


def yearly_returns(net_returns: pd.Series) -> pd.Series:
    return (1.0 + net_returns.fillna(0.0)).groupby(net_returns.index.year).prod() - 1.0
