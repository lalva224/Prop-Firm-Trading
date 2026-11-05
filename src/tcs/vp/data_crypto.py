"""Free Binance 1-minute klines from data.binance.vision.

Binance publishes monthly CSV archives of its own market data at no cost and
with no API key. One HTTP request per month beats ~1000 paginated REST calls.
Cached locally as parquet so backtests are reproducible offline.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"

COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume",
    "ignore",
]


def _fetch_month(symbol: str, interval: str, month: str) -> pd.DataFrame:
    """month as 'YYYY-MM'. Returns empty frame if the archive is absent."""
    url = f"{BASE}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        log.warning("no archive for %s %s", symbol, month)
        return pd.DataFrame()
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            head = fh.read(64)
        has_header = b"open_time" in head
        with zf.open(name) as fh:
            df = pd.read_csv(
                fh,
                header=0 if has_header else None,
                names=None if has_header else COLUMNS,
            )
    return df


def load_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    start: str = "2023-01",
    end: str = "2026-09",
    refresh: bool = False,
) -> pd.DataFrame:
    """OHLCV indexed by UTC open time, plus taker_buy_volume for aggressor split."""
    key = f"klines_{symbol}_{interval}_{start}_{end}"
    path = CACHE_DIR / f"{key}.parquet"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists() and not refresh:
        return pd.read_parquet(path)

    months = pd.period_range(start=start, end=end, freq="M")
    frames = []
    for m in months:
        df = _fetch_month(symbol, interval, str(m))
        if not df.empty:
            frames.append(df)
    if not frames:
        raise RuntimeError(f"no data downloaded for {symbol} {start}..{end}")

    df = pd.concat(frames, ignore_index=True)
    # Binance switched from ms to us timestamps partway through the archives.
    unit = "us" if df["open_time"].max() > 1e15 else "ms"
    df["open_time"] = pd.to_datetime(df["open_time"], unit=unit)
    out = (
        df.set_index("open_time")[
            ["open", "high", "low", "close", "volume", "taker_buy_volume"]
        ]
        .astype(float)
        .sort_index()
    )
    out = out[~out.index.duplicated(keep="first")]
    out.to_parquet(path)
    log.info("loaded %d bars %s -> %s", len(out), out.index[0], out.index[-1])
    return out
