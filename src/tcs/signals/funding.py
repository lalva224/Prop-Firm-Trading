"""Perpetual funding carry - the delta-neutral basis trade.

Mechanic: hold spot long and the perpetual short in equal size. Market
direction cancels out. You collect (or pay) the funding rate every 8 hours.
When funding is positive, longs pay shorts, so the short-perp leg earns.

This is structurally different from everything else in this repo: it is not a
directional forecast. The return does not come from predicting price - it comes
from being paid to provide the other side of leveraged long demand.

Real risks, none of which show up in a funding-rate backtest:
  - exchange counterparty risk (the position lives on the venue)
  - funding flipping negative for extended stretches
  - liquidation of the short leg on a violent rally if margin is thin
  - the trade is crowded, and crowded basis trades unwind together
"""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests

log = logging.getLogger(__name__)

ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"

INTERVALS_PER_YEAR = 365 * 3  # funding paid every 8 hours


def fetch_funding_history(
    symbol: str = "BTCUSDT",
    start: str = "2022-01",
    end: str = "2026-09",
) -> pd.Series:
    """Funding-rate history from Binance's free monthly archives.

    Uses data.binance.vision rather than the fapi REST endpoint: the live API
    returns HTTP 451 from many regions, while the data archive is open. One
    request per month, no API key.
    """
    out: list[pd.Series] = []
    for month in pd.period_range(start=start, end=end, freq="M"):
        url = f"{ARCHIVE}/{symbol}/{symbol}-fundingRate-{month}.zip"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            log.warning("no funding archive for %s %s", symbol, month)
            continue
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as fh:
                head = fh.read(64)
            with zf.open(name) as fh:
                df = pd.read_csv(
                    fh,
                    header=0 if b"calc_time" in head else None,
                    names=None if b"calc_time" in head else
                          ["calc_time", "funding_interval_hours", "last_funding_rate"],
                )
        ts = pd.to_datetime(df["calc_time"].astype("int64"), unit="ms")
        out.append(pd.Series(df["last_funding_rate"].astype(float).to_numpy(), index=ts))

    if not out:
        return pd.Series(dtype=float, name=symbol)
    ser = pd.concat(out).sort_index()
    ser = ser[~ser.index.duplicated(keep="first")]
    ser.name = symbol
    return ser


def carry_returns(funding: pd.Series, side: int = -1) -> pd.Series:
    """Per-interval return of the delta-neutral position.

    side=-1 is short the perp, which EARNS when funding is positive (longs pay
    shorts), so the return is -side * funding = +funding.
    """
    return -side * funding


def annualized(funding: pd.Series) -> float:
    return float(funding.mean() * INTERVALS_PER_YEAR)
