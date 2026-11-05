"""Trade-level backtester for the value-area reversion strategy.

Different engine from backtest.py: that one holds continuous daily weights,
this one takes discrete intraday trades with stops and targets.

Causality and conservatism rules:
  1. The reference profile is the PRIOR session's, complete before this session
     opens. Nothing about the current session leaks into its own levels.
  2. Signals are evaluated on bar close and filled at the NEXT bar's open.
  3. If a single bar's range contains both the stop and the target, the STOP is
     assumed to have filled first. Pessimistic by construction - the opposite
     assumption is the most common way intraday backtests inflate themselves.
  4. Costs and slippage are charged in ticks on every entry and exit.
  5. Positions are closed at session end. No overnight risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .profile import session_profiles


@dataclass
class VPConfig:
    tick_size: float = 0.1
    cost_ticks: float = 1.0        # round-trip commission in ticks
    slippage_ticks: float = 1.0    # per fill
    cost_bps: float = 0.0          # round-trip commission in bps of price.
                                   # Use this, not cost_ticks, for any
                                   # instrument whose price moves over a wide
                                   # range: 1 tick on BTC at $20k and at $100k
                                   # are different costs. Binance perp taker is
                                   # ~4.5bps per side, so ~9bps round trip.
    value_area_pct: float = 0.70
    session: str = "1D"
    stop_atr_mult: float = 1.0     # stop beyond the entry level, in session ATR
    atr_window: int = 14           # sessions
    balance_max_range_atr: float = 1.2   # balance gate: session range vs ATR
    balance_min_va_frac: float = 0.35    # balance gate: VA width / session range
    max_trades_per_session: int = 2
    allow_long: bool = True
    allow_short: bool = True


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int           # +1 long, -1 short
    entry: float
    exit: float
    reason: str         # target | stop | session_end
    pnl_ticks: float
    pnl_bps: float      # PnL as bps of entry price - the comparable unit when
                        # the instrument's price ranges over 5x across the sample


@dataclass
class VPResult:
    trades: pd.DataFrame
    session_pnl: pd.Series
    equity: pd.Series
    stats: dict = field(default_factory=dict)

    def report(self) -> str:
        if self.trades.empty:
            return "no trades taken"
        s = self.stats
        lines = ["", "=" * 46, f"{'metric':<22}{'value':>12}", "-" * 46]
        for k, v in s.items():
            val = f"{v:,.2f}" if isinstance(v, float) else f"{v:,}"
            lines.append(f"{k:<22}{val:>12}")
        lines.append("=" * 46)
        by_reason = self.trades.groupby("reason")["pnl_bps"].agg(["count", "mean"])
        lines.append("\nexits by reason")
        lines.append("-" * 46)
        for reason, row in by_reason.iterrows():
            lines.append(f"  {reason:<16}{int(row['count']):>6}  "
                         f"avg {row['mean']:>8.1f} bps")
        return "\n".join(lines)


def _stats(trades: pd.DataFrame, session_pnl: pd.Series, cfg: VPConfig) -> dict:
    wins = trades.loc[trades.pnl_bps > 0, "pnl_bps"]
    losses = trades.loc[trades.pnl_bps <= 0, "pnl_bps"]
    gross_win, gross_loss = wins.sum(), abs(losses.sum())
    sd = session_pnl.std()
    return {
        "n_trades": int(len(trades)),
        "hit_rate": float((trades.pnl_bps > 0).mean()),
        "avg_win_bps": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_bps": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_bps": float(trades.pnl_bps.mean()),
        "total_bps": float(trades.pnl_bps.sum()),
        "reward_risk": float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() else float("nan"),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "sharpe_sessions": float(session_pnl.mean() / sd * np.sqrt(252)) if sd else 0.0,
        "max_dd_bps": float((session_pnl.cumsum() - session_pnl.cumsum().cummax()).min()),
        "n_sessions": int(len(session_pnl)),
    }


def run_vp(
    bars: pd.DataFrame,
    cfg: VPConfig | None = None,
    profiles: pd.DataFrame | None = None,
) -> VPResult:
    """Run the balance / value-area reversion strategy over 1m OHLCV bars.

    `profiles` may be passed in precomputed: session profiles do not depend
    on any of the trading parameters, so a sweep should build them once.
    """
    cfg = cfg or VPConfig()

    prof = (
        profiles.copy()
        if profiles is not None
        else session_profiles(bars, cfg.tick_size, cfg.value_area_pct, cfg.session)
    )
    if prof.empty or len(prof) < cfg.atr_window + 2:
        return VPResult(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float))

    prof["range"] = prof["high"] - prof["low"]
    prof["atr"] = prof["range"].rolling(cfg.atr_window).mean()
    prof["va_frac"] = (prof["vah"] - prof["val"]) / prof["range"].where(prof["range"] > 0)

    # Balance gate, evaluated on the PRIOR session only - known before we trade.
    balanced = (
        (prof["range"] <= cfg.balance_max_range_atr * prof["atr"])
        & (prof["va_frac"] >= cfg.balance_min_va_frac)
    )
    ref = prof.assign(balanced=balanced).shift(1)  # prior session's levels & gate

    trades: list[Trade] = []
    slip = cfg.slippage_ticks * cfg.tick_size

    for session_ts, sess in bars.groupby(pd.Grouper(freq=cfg.session)):
        if sess.empty or session_ts not in ref.index:
            continue
        r = ref.loc[session_ts]
        if not bool(r.get("balanced", False)) or not np.isfinite(r.get("poc", np.nan)):
            continue
        poc, vah, val, atr = r["poc"], r["vah"], r["val"], r["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue

        stop_dist = cfg.stop_atr_mult * atr
        side = 0
        entry = target = stop = np.nan
        entry_time = None
        n_trades = 0

        o = sess["open"].to_numpy(float)
        h = sess["high"].to_numpy(float)
        l = sess["low"].to_numpy(float)
        c = sess["close"].to_numpy(float)
        idx = sess.index

        for i in range(len(sess) - 1):
            if side == 0:
                if n_trades >= cfg.max_trades_per_session:
                    break
                # Rule 2: decide on close of bar i, fill at open of bar i+1.
                # Rule 6: reject a fill that is already past its own stop or
                # target. Price can open far beyond the prior session's VAH, in
                # which case a short would sit ABOVE its stop and "stopping
                # out" would book a profit - a pure artefact that inflates
                # results. The trade is simply not available at that price.
                if cfg.allow_short and c[i] >= vah:
                    px, st, tg = o[i + 1] - slip, vah + stop_dist, poc
                    if tg < px < st:
                        side, entry, stop, target = -1, px, st, tg
                elif cfg.allow_long and c[i] <= val:
                    px, st, tg = o[i + 1] + slip, val - stop_dist, poc
                    if st < px < tg:
                        side, entry, stop, target = 1, px, st, tg
                if side != 0:
                    entry_time = idx[i + 1]
                    n_trades += 1
                continue

            # In position: rule 3 - stop checked before target.
            exit_px = exit_reason = None
            if side == -1:
                if h[i] >= stop:
                    exit_px, exit_reason = stop + slip, "stop"
                elif l[i] <= target:
                    exit_px, exit_reason = target + slip, "target"
            else:
                if l[i] <= stop:
                    exit_px, exit_reason = stop - slip, "stop"
                elif h[i] >= target:
                    exit_px, exit_reason = target - slip, "target"

            if exit_px is not None:
                cost = cfg.cost_ticks * cfg.tick_size + entry * cfg.cost_bps / 1e4
                pnl = (exit_px - entry) * side - cost
                trades.append(Trade(entry_time, idx[i], side, entry, exit_px,
                                    exit_reason, pnl / cfg.tick_size,
                                    pnl / entry * 1e4))
                side = 0

        # Rule 5: flatten at session end.
        if side != 0:
            exit_px = c[-1] - slip * side
            cost = cfg.cost_ticks * cfg.tick_size + entry * cfg.cost_bps / 1e4
            pnl = (exit_px - entry) * side - cost
            trades.append(Trade(entry_time, idx[-1], side, entry, exit_px,
                                "session_end", pnl / cfg.tick_size,
                                pnl / entry * 1e4))

    if not trades:
        return VPResult(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float))

    tdf = pd.DataFrame([t.__dict__ for t in trades])
    session_pnl = (
        tdf.set_index("exit_time")["pnl_bps"].groupby(pd.Grouper(freq=cfg.session)).sum()
    )
    return VPResult(
        trades=tdf,
        session_pnl=session_pnl,
        equity=session_pnl.cumsum(),
        stats=_stats(tdf, session_pnl, cfg),
    )
