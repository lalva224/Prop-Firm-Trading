"""Tests for the broker-export loader, using synthetic CSVs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tcs import mt5_data


def make_export(d: Path, symbols: dict[str, int], with_bond: bool = False):
    """symbols: {name: n_bars}. Writes CSVs plus the _index.json."""
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    entries = []
    for sym, n in symbols.items():
        idx = pd.bdate_range("2010-01-01", periods=n)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n)))
        df = pd.DataFrame({
            "date": idx.strftime("%Y.%m.%d"),
            "open": close, "high": close * 1.004, "low": close * 0.996,
            "close": close, "tick_volume": 1000, "real_volume": 0, "spread": 20,
        })
        df.to_csv(d / f"{sym}.csv", index=False)
        path = "Bonds\\Government" if (with_bond and sym == "BUND") else "Forex\\Majors"
        desc = "Euro Bund future" if (with_bond and sym == "BUND") else f"{sym} spot"
        entries.append({"symbol": sym, "bars": n, "first": str(idx[0].date()),
                        "last": str(idx[-1].date()), "path": path, "description": desc,
                        "point": 0.001, "digits": 3, "volume_min": 0.01,
                        "volume_step": 0.01, "contract_size": 100.0})
    (d / "_index.json").write_text(json.dumps(
        {"schema": 1, "timeframe": "PERIOD_D1", "symbols": entries,
         "exported": len(entries), "skipped": 0}))


def test_loads_ohlc_in_the_same_shape_as_yfinance(tmp_path):
    make_export(tmp_path, {"EURUSD": 2000, "XAUUSD": 2000})
    out = mt5_data.load_ohlc(tmp_path, min_bars=100)
    assert set(out) >= {"open", "high", "low", "close", "spread_bps"}
    assert list(out["close"].columns) == ["EURUSD", "XAUUSD"]
    assert isinstance(out["close"].index, pd.DatetimeIndex)


def test_short_history_symbols_are_dropped(tmp_path):
    make_export(tmp_path, {"GOOD": 2000, "TOOSHORT": 50})
    out = mt5_data.load_ohlc(tmp_path, min_bars=500)
    assert "GOOD" in out["close"].columns
    assert "TOOSHORT" not in out["close"].columns


def test_spread_is_converted_to_bps(tmp_path):
    """spread is in POINTS, so bps = spread * point / close * 1e4.

    The expectation is derived from the data rather than assumed, because the
    synthetic close is a random walk and does not stay at its starting price.
    """
    make_export(tmp_path, {"EURUSD": 1000})
    out = mt5_data.load_ohlc(tmp_path, min_bars=100)
    bps = out["spread_bps"]["EURUSD"].dropna()
    close = out["close"]["EURUSD"].reindex(bps.index)
    expected = (20 * 0.001 / close * 1e4).median()
    assert (bps > 0).all()
    assert bps.median() == pytest.approx(expected, rel=1e-9)
    # And sanity: a 20-point spread on a 3-digit symbol is single-digit bps,
    # not thousands. That is the error the point conversion prevents.
    assert bps.median() < 10, bps.median()


def test_missing_point_gives_nan_not_a_wrong_number(tmp_path):
    """A mis-scaled spread is worse than no spread: it would silently make
    every instrument look untradeable."""
    make_export(tmp_path, {"EURUSD": 1000})
    idx = json.loads((tmp_path / "_index.json").read_text())
    for e in idx["symbols"]:
        e.pop("point"); e.pop("digits")
    (tmp_path / "_index.json").write_text(json.dumps(idx))
    out = mt5_data.load_ohlc(tmp_path, min_bars=100)
    assert out["spread_bps"]["EURUSD"].isna().all()


def test_coverage_reports_years(tmp_path):
    make_export(tmp_path, {"A": 2600, "B": 600})
    txt = mt5_data.coverage(tmp_path)
    assert "symbols exported   2" in txt
    assert "median history" in txt


def test_classify_surfaces_a_bond_when_one_exists(tmp_path):
    make_export(tmp_path, {"EURUSD": 1000, "BUND": 1000}, with_bond=True)
    cls = mt5_data.classify(tmp_path)
    assert "Bonds" in set(cls["group"])
    assert cls.loc[cls.symbol == "BUND", "group"].iloc[0] == "Bonds"


def test_classify_is_empty_when_no_bond_exists(tmp_path):
    make_export(tmp_path, {"EURUSD": 1000, "XAUUSD": 1000})
    cls = mt5_data.classify(tmp_path)
    assert "Bonds" not in set(cls["group"])


def test_missing_directory_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        mt5_data.load_ohlc(tmp_path / "nope")


def test_no_index_file_is_handled(tmp_path):
    make_export(tmp_path, {"A": 1000})
    (tmp_path / "_index.json").unlink()
    assert mt5_data.read_index(tmp_path).empty
    assert "run DumpHistory" in mt5_data.coverage(tmp_path)


def test_downstream_pipeline_runs_unchanged_on_broker_data(tmp_path):
    """The whole point: signals/backtest must work with no code edits."""
    from tcs.backtest import BacktestConfig, run
    from tcs.signals import ewmac_blend

    make_export(tmp_path, {f"SYM{i}": 2600 for i in range(6)})
    ohlc = mt5_data.load_ohlc(tmp_path, min_bars=500)
    res = run(ohlc["close"], lambda p: ewmac_blend(p, smooth=10),
              BacktestConfig(cost_bps=2.0))
    assert res.stats["n_days"] > 1000
    assert np.isfinite(res.stats["sharpe"])
