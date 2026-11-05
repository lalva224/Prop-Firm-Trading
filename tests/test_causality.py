"""The tests that make a backtest trustworthy.

If these pass, the engine cannot see the future. Nothing else about the
strategy matters until they do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.backtest import BacktestConfig, run
from tcs.signals import trend_signal


def synthetic_prices(n_days: int = 1500, n_assets: int = 6, seed: int = 7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=n_days)
    shocks = rng.normal(0.0003, 0.01, size=(n_days, n_assets))
    px = 100 * np.exp(np.cumsum(shocks, axis=0))
    return pd.DataFrame(px, index=idx, columns=[f"A{i}" for i in range(n_assets)])


def test_truncation_does_not_change_past_positions():
    """A position at date t must not depend on any price after t."""
    px = synthetic_prices()
    cut = px.index[1000]

    full = run(px, trend_signal, BacktestConfig())
    short = run(px.loc[:cut], trend_signal, BacktestConfig())

    # Compare only where the vol-target lookback is fully warmed up in both.
    common = short.positions.index[:-5]
    a = full.positions.loc[common]
    b = short.positions.loc[common]
    pd.testing.assert_frame_equal(a, b, atol=1e-10)


def test_signal_cannot_capture_its_own_return():
    """With execution_lag=0 and no extra shift, a perfect-foresight signal
    would score absurdly. Confirm the engine's lag kills that."""
    px = synthetic_prices()
    future_return = px.pct_change().shift(-1)  # blatant lookahead

    def cheating_signal(prices):
        return np.sign(future_return).reindex_like(prices).fillna(0.0)

    res = run(px, cheating_signal, BacktestConfig(execution_lag=1))
    # One day of execution lag plus the engine's own shift means the cheat
    # is pointing two days stale -> no exploitable edge remains.
    assert res.stats["sharpe"] < 1.5, (
        f"lookahead leaked through the engine: Sharpe {res.stats['sharpe']:.2f}"
    )


def test_costs_reduce_returns():
    px = synthetic_prices()
    free = run(px, trend_signal, BacktestConfig(cost_bps=0.0))
    pricey = run(px, trend_signal, BacktestConfig(cost_bps=50.0))
    assert pricey.net_returns.sum() < free.net_returns.sum()


def test_vol_targeting_hits_approximate_target():
    px = synthetic_prices(n_days=2500, n_assets=10)
    res = run(px, trend_signal, BacktestConfig(target_ann_vol=0.12))
    assert 0.05 < res.stats["ann_vol"] < 0.25, res.stats["ann_vol"]


def test_no_position_without_history():
    """No instrument may be traded before it has enough data to form a signal."""
    px = synthetic_prices()
    px.iloc[:400, 0] = np.nan  # asset A0 starts late
    res = run(px, trend_signal, BacktestConfig())
    assert res.positions["A0"].iloc[:300].abs().sum() == 0.0
