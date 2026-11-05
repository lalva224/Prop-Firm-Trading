"""Causality and sanity tests for the value-area reversion engine."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs.vp import VPConfig, build_profile, profile_from_trades, run_vp
from tcs.vp.profile import session_profiles


def synthetic_bars(n_days: int = 60, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_days * 1440
    idx = pd.date_range("2024-01-01", periods=n, freq="1min")
    mid = 100 * np.exp(np.cumsum(rng.normal(0, 0.0004, n)))
    spread = np.abs(rng.normal(0, 0.05, n))
    return pd.DataFrame(
        {
            "open": mid,
            "high": mid + spread,
            "low": mid - spread,
            "close": mid + rng.normal(0, 0.02, n),
            "volume": np.abs(rng.normal(100, 30, n)),
        },
        index=idx,
    )


def test_poc_is_the_highest_volume_price():
    trades = pd.DataFrame({"price": [10.0, 10.0, 10.1, 10.2, 10.2, 10.2], "size": [1, 1, 5, 9, 9, 9]})
    p = profile_from_trades(trades, tick_size=0.1)
    assert p.poc == pytest.approx(10.2)
    assert p.val <= p.poc <= p.vah


def test_value_area_encloses_requested_share():
    bars = synthetic_bars(2)
    p = build_profile(bars, tick_size=0.01, value_area_pct=0.70)
    inside = p.bins[(p.bins.index >= p.val) & (p.bins.index <= p.vah)].sum()
    assert inside / p.total_volume >= 0.70 - 1e-9


def test_vectorised_matches_naive_profile():
    """Guards the vectorised rewrite against the straightforward loop."""
    bars = synthetic_bars(1).iloc[:500]
    tick = 0.01
    p = build_profile(bars, tick)
    acc: dict[float, float] = {}
    for lo, hi, vol in zip(bars["low"], bars["high"], bars["volume"]):
        lo_b, hi_b = round(lo / tick) * tick, round(hi / tick) * tick
        n = int(round((hi_b - lo_b) / tick)) + 1
        step = (hi_b - lo_b) / (n - 1) if n > 1 else 0.0
        for k in range(n):
            key = round((lo_b + k * step) / tick) * tick
            acc[key] = acc.get(key, 0.0) + vol / n
    naive = pd.Series(acc).sort_index()
    assert p.poc == pytest.approx(naive.idxmax(), abs=tick)
    assert p.total_volume == pytest.approx(naive.sum(), rel=1e-9)


def test_profiles_use_only_prior_session():
    """Truncating future sessions must not change any earlier trade."""
    bars = synthetic_bars(60)
    cut = "2024-02-10"
    cfg = VPConfig(tick_size=0.01)
    full = run_vp(bars, cfg).trades
    short = run_vp(bars.loc[:cut], cfg).trades
    if full.empty or short.empty:
        pytest.skip("no trades generated on synthetic data")
    common = short.entry_time.max()
    a = full[full.entry_time <= common].reset_index(drop=True)
    b = short[short.entry_time <= common].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_no_fill_beyond_its_own_stop():
    """Rule 6: a stop exit can never be profitable."""
    bars = synthetic_bars(90)
    r = run_vp(bars, VPConfig(tick_size=0.01, stop_atr_mult=0.1))
    if r.trades.empty:
        pytest.skip("no trades generated on synthetic data")
    stops = r.trades[r.trades.reason == "stop"]
    assert (stops.pnl_bps <= 0).all(), (
        f"{(stops.pnl_bps > 0).sum()} profitable stop exits - rule 6 violated"
    )


def test_costs_reduce_expectancy():
    bars = synthetic_bars(90)
    cheap = run_vp(bars, VPConfig(tick_size=0.01, cost_ticks=0.0, slippage_ticks=0.0))
    dear = run_vp(bars, VPConfig(tick_size=0.01, cost_ticks=20.0, slippage_ticks=10.0))
    if cheap.trades.empty:
        pytest.skip("no trades generated on synthetic data")
    assert dear.trades.pnl_bps.mean() < cheap.trades.pnl_bps.mean()
