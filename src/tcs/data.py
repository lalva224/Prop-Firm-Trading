"""Daily price loading with on-disk cache.

Free daily OHLC is all the Tier-1 strategies need. We cache to parquet so
backtests are reproducible and offline-runnable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.parquet"


def load_prices(
    tickers: list[str],
    start: str = "2005-01-01",
    end: str | None = None,
    cache_key: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Adjusted close prices, columns=tickers, DatetimeIndex, forward-filled.

    Dividend-adjusted so total-return series are used for ETFs. Columns with
    fewer than 500 observations are dropped as unusable.
    """
    key = cache_key or f"px_{abs(hash(tuple(sorted(tickers))))}_{start}"
    path = _cache_path(key)

    if path.exists() and not refresh:
        df = pd.read_parquet(path)
    else:
        import warnings

        with warnings.catch_warnings():
            # yfinance uses inplace= internally; not actionable for us.
            warnings.simplefilter('ignore')
            import yfinance as yf

            raw = yf.download(
                tickers,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                group_by="column",
            )
        if isinstance(raw.columns, pd.MultiIndex):
            df = raw["Close"].copy()
        else:  # single ticker
            df = raw[["Close"]].copy()
            df.columns = tickers
        df = df.sort_index()
        df.to_parquet(path)

    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)

    # Only forward-fill inside each column's own live range; never backfill.
    df = df.ffill()
    usable = df.notna().sum() >= 500
    dropped = [c for c in df.columns if not usable.get(c, False)]
    if dropped:
        log.warning("dropping columns with insufficient history: %s", dropped)
    return df.loc[:, usable[usable].index]


def to_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple daily returns. First row is NaN by construction."""
    return prices.pct_change()


def fetch_binance_funding(symbol: str, limit: int = 1000) -> pd.DataFrame:
    """Historical funding rates for a Binance USDT perp. Free, no API key.

    Funding is paid every 8h. Positive funding = longs pay shorts, i.e. being
    short the perp earns carry. Returns DataFrame indexed by UTC timestamp with
    a 'funding_rate' column (per-interval, not annualized).
    """
    import requests

    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    resp = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame(columns=["funding_rate"])
    df = pd.DataFrame(rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df.set_index("fundingTime")[["funding_rate"]].sort_index()


def load_ohlc(
    tickers: list[str],
    start: str = "2005-01-01",
    end: str | None = None,
    cache_key: str | None = None,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Open/High/Low/Close frames, each columns=tickers.

    High and Low are what make a faithful prop-firm simulation possible: FTMO
    evaluates EQUITY INCLUDING FLOATING P&L, so a position that is down 5%
    intraday breaches even if it closes flat. Close-only data cannot see that
    and will understate breach probability.
    """
    key = cache_key or f"ohlc_{abs(hash(tuple(sorted(tickers))))}_{start}"
    path = _cache_path(key)

    if path.exists() and not refresh:
        wide = pd.read_parquet(path)
    else:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import yfinance as yf

            wide = yf.download(
                tickers, start=start, end=end, auto_adjust=True,
                progress=False, group_by="column",
            )
        wide = wide.sort_index()
        wide.to_parquet(path)

    wide.index = pd.to_datetime(wide.index)
    if getattr(wide.index, "tz", None) is not None:
        wide.index = wide.index.tz_localize(None)

    out: dict[str, pd.DataFrame] = {}
    for field in ("Open", "High", "Low", "Close"):
        if isinstance(wide.columns, pd.MultiIndex):
            df = wide[field].copy()
        else:
            df = wide[[field]].copy()
            df.columns = tickers
        out[field.lower()] = df.ffill()

    usable = out["close"].notna().sum() >= 500
    keep = usable[usable].index
    dropped = [c for c in out["close"].columns if c not in keep]
    if dropped:
        log.warning("dropping columns with insufficient history: %s", dropped)
    return {k: v.loc[:, keep] for k, v in out.items()}
