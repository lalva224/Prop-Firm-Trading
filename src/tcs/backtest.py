"""Daily backtest engine.

Causality rules enforced here, because these are the three ways a systematic
backtest lies to you:

  1. EXECUTION LAG. Signals computed from the close of day t are traded at the
     close of day t+1. Positions are shifted one extra day before being
     multiplied by returns, so no position can profit from the return that
     generated it.
  2. NO BACKFILL. Prices are only forward-filled (see data.load_prices), never
     back-filled, so an instrument cannot trade before it existed.
  3. COSTS ON TURNOVER. Every weight change is charged. Zero-cost backtests of
     daily strategies are not optimistic, they are meaningless.

Test coverage for rule 1 lives in tests/test_causality.py - truncating the
input must not change any position computed before the truncation point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import metrics
from .portfolio import (
    buffer_positions,
    ewma_vol,
    risk_parity_positions,
    vol_target_scalar,
)

SignalFn = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass
class BacktestConfig:
    target_ann_vol: float = 0.12
    gross_cap: float = 1.0
    max_leverage: float = 6.0
    cost_bps: float = 2.0  # per unit of turnover, one way
    buffer_frac: float = 0.30  # no-trade band, as a fraction of typical size
    vol_halflife: int = 32
    vol_target_lookback: int = 252
    execution_lag: int = 1  # days between signal and fill


@dataclass
class BacktestResult:
    positions: pd.DataFrame
    gross_returns: pd.Series
    net_returns: pd.Series
    turnover: pd.Series
    equity: pd.Series
    stats: dict = field(default_factory=dict)

    def report(self) -> str:
        lines = ["", "=" * 46, f"{'metric':<18}{'value':>12}", "-" * 46]
        fmt = {
            "cagr": "{:.2%}", "ann_vol": "{:.2%}", "max_dd": "{:.2%}",
            "hit_rate": "{:.2%}", "sharpe": "{:.2f}", "sortino": "{:.2f}",
            "calmar": "{:.2f}", "skew": "{:.2f}", "ann_turnover": "{:.1f}x",
            "n_days": "{:d}",
        }
        for k, v in self.stats.items():
            lines.append(f"{k:<18}{fmt.get(k, '{:.3f}').format(v):>12}")
        lines.append("=" * 46)
        yr = metrics.yearly_returns(self.net_returns)
        lines.append("\nper-year net return")
        lines.append("-" * 46)
        for year, r in yr.items():
            bar = "#" * min(40, int(abs(r) * 100))
            lines.append(f"{year}  {r:>8.2%}  {bar}")
        return "\n".join(lines)


def run(
    prices: pd.DataFrame,
    signal_fn: SignalFn,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    cfg = config or BacktestConfig()

    returns = prices.pct_change()
    signals = signal_fn(prices)
    signals = signals.reindex_like(returns).fillna(0.0)

    vols = ewma_vol(returns, halflife=cfg.vol_halflife, annualize=True)
    weights = risk_parity_positions(signals, vols, gross_cap=cfg.gross_cap)

    scalar = vol_target_scalar(
        weights,
        returns,
        target_ann_vol=cfg.target_ann_vol,
        lookback=cfg.vol_target_lookback,
        max_leverage=cfg.max_leverage,
    )
    positions = weights.mul(scalar, axis=0)
    if cfg.buffer_frac > 0:
        positions = buffer_positions(positions, cfg.buffer_frac)

    # Rule 1: signal from close t is filled at close t+lag, so the position is
    # only exposed to returns from t+lag+1 onward.
    held = positions.shift(cfg.execution_lag)
    gross = (held.shift(1) * returns).sum(axis=1)

    turnover = (held - held.shift(1)).abs().sum(axis=1).fillna(0.0)
    costs = turnover * cfg.cost_bps / 1e4
    net = gross - costs

    # Trim the warm-up period where no position was ever taken.
    live = held.abs().sum(axis=1) > 0
    if live.any():
        first = live.idxmax()
        gross, net, turnover = gross.loc[first:], net.loc[first:], turnover.loc[first:]

    equity = (1.0 + net.fillna(0.0)).cumprod()
    stats = metrics.summary(net, turnover)

    return BacktestResult(
        positions=positions,
        gross_returns=gross,
        net_returns=net,
        turnover=turnover,
        equity=equity,
        stats=stats,
    )
