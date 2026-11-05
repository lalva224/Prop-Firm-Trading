# MT5 as the Research Data Source

Built 2026-10-27. Replaces yfinance proxies with the broker's own history for
the exact instruments the live strategy will trade.

```
# in MT5 (Windows), run each ONCE as a Script - not an EA
mql5/DumpSymbols.mq5     -> MQL5\Files\tcs_all_symbols.json
mql5/DumpHistory.mq5     -> MQL5\Files\tcs_history\*.csv + _index.json

# then on the mini PC
python3 scripts/analyze_mt5.py --dir "<...>/MQL5/Files/tcs_history"
python3 scripts/analyze_mt5.py --dir ... --backtest --ftmo
```

## Why this is better than buying data

Two confounds disappear at once, for free:

**1. Roll artefacts.** Every commodity measurement so far used yfinance
front-month continuous futures, which are **not back-adjusted** - each contract
roll injects a fake gap directly into the signal and the P&L. This is the
confound that made the futures universe measure Sharpe 0.40 against the ETF
universe's 0.82, and the ag sleeve measure -0.39. **Broker CFD series are
continuous by construction.** The $30-150 data purchase existed solely to fix
this; the broker export fixes it at zero cost.

**2. Proxy mismatch.** `^GSPC` is a price index with no dividend adjustment
(measured drag: -1.98%/yr) and `EURUSD=X` is spot with no carry - which is why
the FX-ETF sleeve measured +0.47 while spot FX measured -0.20. The broker's
`US500` and `EURUSD` are the actual tradeable instruments, with the actual
spreads, at the actual symbol names the EA will use.

## What it costs

**History depth is whatever the broker holds**, which may be well short of 21
years. `mt5_data.coverage()` reports it precisely. The rule of thumb printed
there: a trend system needs 10y+ to span more than one regime, and fewer than
~15 symbols at 10y+ means this supplements a paid feed rather than replacing it.

Also: CFD prices embed the broker's markup, so returns are very slightly worse
than the underlying. That is a feature here - it is the price you will actually
trade at.

## Questions the analyzer answers, in order

1. **How much history did the broker give us?**
2. **Does ANY bond or rate instrument exist?** Grouped by the broker's own
   `SYMBOL_PATH` tree and keyword-matched against bond/bund/gilt/treasury/note/
   BTP/OAT/JGB/schatz/bobl. The bond-credit sleeve measured **Sharpe 0.87**, the
   best component in the whole system; every published list says FTMO has none.
   Confirm it from the account, not from a review site.
3. **What do the instruments cost in spread?** Measured, not assumed, and fed
   straight into the backtest's `cost_bps`.
4. **What are the minimum position sizes?** Determines how many instruments a
   $100k account can actually hold.
5. **On clean data, does the strategy have an edge?** Full / in-sample /
   out-of-sample, plus effective independent bets and the Sharpe standard error
   so differences inside the noise floor are not over-read.
6. **With `--ftmo`: the challenge EV**, across all three intraday models, at the
   real fee ($619/$100k = 0.619%), against the pre-registered 2.0x gate.

## A bug worth recording

`MqlRates.spread` is in **POINTS, not price units**. The first version computed
`spread / close * 1e4`, which for a 5-digit FX pair is wrong by a factor of
100,000 and would make every instrument look untradeable. The fix exports
`SYMBOL_POINT` per symbol and computes `spread * point / close * 1e4`.

When the point size is missing the loader returns **NaN rather than a number** -
a silently mis-scaled cost is worse than a visibly absent one.
`test_missing_point_gives_nan_not_a_wrong_number` locks that in.

## Downstream compatibility

`mt5_data.load_ohlc` returns the same dict shape as `data.load_ohlc`, so
signals, backtest, the FTMO harness, the guard and the bridge all work on broker
data with **no code edits**. `test_downstream_pipeline_runs_unchanged_on_broker_data`
asserts it.

## Practical notes for running the dumps

- MT5 only serves history it has already **downloaded**. `DumpHistory.mq5`
  retries per symbol (25 attempts, 400ms apart) until the count stops growing.
  A first run on a fresh terminal can take a while and may return short series;
  run it twice.
- Set `InpMarketWatchOnly = false` to get everything the broker lists, not just
  what is in Market Watch.
- Symbol names containing `/ \ : * ? " < > |` are sanitised for filenames, so
  the CSV stem may differ slightly from the MT5 symbol. `_index.json` carries
  the true name.
