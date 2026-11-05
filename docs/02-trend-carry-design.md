# Trend + Carry System: Design and Results

Built 2026-10-27. Reproduce with:

```bash
.venv/bin/python scripts/run_backtest.py --universe etf
```

## Why this and not order flow

The strategies with the strongest documented evidence run on **free daily data**;
the strategies that need expensive tick/MBO feeds are the ones a non-colocated
retail participant cannot win. For retail, the correlation between data cost and
strategy viability is **negative**. This system therefore spends nothing on data.

Edge source is risk premium plus behavioural bias, **not speed** - which is
precisely why it has survived a century of being publicly known.

## Architecture

```
data.py      free daily adjusted closes, parquet-cached, ffill only (never bfill)
signals/
  trend.py   ewmac_blend  - EWMA crossovers / vol, tanh-squashed, smoothed  [PRIMARY]
             trend_signal - lookback returns / vol (noisier, 4x the turnover)
  carry.py   funding_carry_signal - crypto perp funding carry  [IMPLEMENTED]
             futures_term_carry   - needs contract-level data  [STUBBED, NOT FAKED]
portfolio.py ewma_vol, risk_parity_positions, vol_target_scalar, buffer_positions
backtest.py  daily engine with enforced execution lag and turnover costs
metrics.py   stats + deflated_sharpe_hint (multiple-testing haircut)
```

### Causality rules enforced in the engine
1. **Execution lag** - signal from close t fills at close t+1; positions are
   shifted again before meeting returns, so no position can earn the return
   that created it.
2. **No backfill** - prices forward-filled only; an instrument cannot trade
   before it existed.
3. **Costs on all turnover** - every weight change is charged.

`tests/test_causality.py` is the load-bearing test file. It verifies that
truncating the input leaves all prior positions bit-identical, that a
deliberate perfect-foresight signal earns nothing through the engine, that costs
reduce returns, and that no instrument trades before it has history. **If these
ever fail, every number below is void.**

## Results — ETF universe, 20 instruments, 2005-01-03 to 2026-10-27

Config: ewmac_blend((16,64),(32,128),(64,256)), smooth=10, buffer=0.30,
target vol 12%, max leverage 6x, 2bps per unit turnover.

| metric | value |
|---|---|
| CAGR | **10.22%** |
| Ann. vol | 12.91% |
| **Sharpe** | **0.82** |
| Sortino | 1.05 |
| Max drawdown | -25.56% |
| Calmar | 0.40 |
| Skew | -0.38 |
| Hit rate | 55.3% |
| Ann. turnover | 13.6x |
| Cost drag @2bps | 0.27%/yr (3% of gross) |

Per-year returns show the textbook trend-following signature, which is itself
evidence the implementation is not broken: **+19.6% in 2008** and **+21.5% in
2022** (crisis alpha), against **-11.6% in 2020** and **-14.0% in 2023** (sharp
V-reversals, trend's known failure mode).

### The turnover fix — why the first version was wrong

The initial build used lookback-return momentum and produced **113x annual
turnover**, a 2.3%/yr cost drag against a 5% CAGR. Decomposition showed the
vol-target scalar was innocent (1.0x turnover) and the churn came from the
signal itself: the 21-day lookback caused 0.074 mean daily change on a [-1,1]
signal as old prices rolled discontinuously out of the window.

| variant | Sharpe | CAGR | turnover | cost drag |
|---|---|---|---|---|
| lookback(21,63,252) | 0.53 | 5.58% | 78.8x | 1.58% |
| lookback(63,252) | 0.67 | 7.28% | 44.5x | 0.89% |
| ewmac blend | 0.74 | 7.92% | 13.7x | 0.27% |
| ewmac + smooth10 | 0.75 | 7.98% | 11.1x | 0.22% |
| **+ leverage cap 6x** | **0.82** | **10.22%** | 13.6x | 0.27% |

EWMA crossovers are smoother because no single past price leaves the window
abruptly. The leverage cap mattered because at 3x the vol target was pinned and
non-binding more than half the time - the system was running fixed-leverage, not
vol-targeted, and merely happened to land near 12%.

## Validation and the bad news

**Time split (parameters chosen on the full ETF sample, so this is a soft test):**

| period | Sharpe | CAGR | vol | max DD |
|---|---|---|---|---|
| in-sample 2005-2017 | 0.95 | 12.17% | 13.04% | -16.07% |
| held-out 2018-2026 | **0.63** | 7.49% | 12.72% | -25.43% |

Degradation from 0.95 to 0.63 is consistent with mild selection effects plus the
genuinely poor trend environment of the late 2010s. Still positive, still
respectable - but **0.63 is the number to plan around, not 0.82.**

**Futures universe, same locked parameters: Sharpe 0.40, CAGR 4.43%.**

This looks like a refutation and must not be dismissed, but it is **confounded**:
yfinance continuous futures series are not properly back-adjusted, so each
contract roll injects a spurious gap return directly into the signal and the
P&L. The test cannot be resolved without back-adjusted contract data
(Databento / CSI / Norgate). **Treat the futures number as uninterpretable
rather than as evidence either way, and resolve it before committing capital** -
futures are the intended live vehicle, so this is the single most important open
question in the project.

**Multiple-testing accounting:** 9 variants tried to date (4 buffer values, 5
signal variants). Expected maximum Sharpe from pure noise at 9 trials over 5363
observations is **0.25**. Observed 0.82 in-sample / 0.63 held-out clears that bar
comfortably. Keep incrementing `--trials` cumulatively for every future test; a
Sharpe quoted without the trial count is not evidence.

## Known limitations

- **Correlated universe.** Six of twenty ETFs are equity beta. Risk parity
  equalises per-instrument vol but not per-asset-class risk, so the book is
  implicitly overweight equities. Asset-class-level risk budgeting is the
  clearest remaining structural improvement.
- **No carry sleeve live.** Trend only. Futures term-structure carry needs
  contract-level data; crypto funding carry is implemented but not yet wired
  into the portfolio.
- **Costs are an assumption.** 2bps per unit turnover is reasonable for liquid
  ETFs and optimistic for some futures. At 13.6x turnover the result is not
  cost-fragile (3% of gross), which is the main benefit of the turnover fix.
- **ETFs are the wrong live vehicle.** No leverage, no shorting ease, worse tax
  treatment than futures. They were chosen as a clean validation substrate.
- **Expected real-world Sharpe is lower than any number here.** Slippage,
  execution timing, and regime change all subtract.

## The arithmetic that matters

Sharpe 0.63 at 12% vol is roughly **7-8%/yr**. On $25k that is ~$2k/yr; on
$250k, ~$20k. The algorithm is indifferent to account size - your life is not.
Multi-year flat stretches (see 2012, 2016, 2018) are the normal experience, and
abandoning the system during one is the dominant failure mode, far more than
any defect in the code.

## Next steps, in priority order

1. **Resolve the futures discrepancy** with back-adjusted contract data. Highest
   information value in the project.
2. **Asset-class risk budgeting** - cap equity-bucket risk explicitly.
3. **Wire in the crypto funding-carry sleeve** - already implemented, free data,
   genuinely uncorrelated to trend.
4. **Walk-forward validation** - expanding-window refits rather than one split.
5. **Paper trade 1-3 months** before any capital.

---

# Addendum: regime-conditional mean reversion (2026-10-27)

**Hypothesis.** Trend following loses in sideways/choppy markets; mean reversion
loses in strong trends. Blend them by a continuous per-instrument trendiness
score rather than a binary regime switch (which would whipsaw at the boundary).

Implemented in `signals/regime.py` (Kaufman efficiency ratio + Lo-MacKinlay
variance ratio, each rank-normalised against the instrument's own 2y history),
`signals/reversion.py`, and `signals/combine.py`.

**Pre-registered prediction, stated before running:** trend-only lost -11.62%
in 2020 and -14.04% in 2023, both sharp V-reversal years. If the reversion
sleeve does what we think, it must improve *those specific years*.

## Result 1 — the prediction passed

| rev risk share | 2020 | 2023 | verdict |
|---|---|---|---|
| 0.15 | -6.71% (+4.91%) | -13.73% (+0.32%) | PASS |
| 0.30 | -6.67% (+4.95%) | -12.72% (+1.32%) | PASS |
| 0.50 | -3.81% (+7.81%) | -11.32% (+2.72%) | PASS |

The mechanism is confirmed: the sleeve hedges trend's whipsaw failure mode,
and the two sleeves' returns correlate **-0.223**.

## Result 2 — but the sleeve has NEGATIVE GROSS EDGE

Run standalone with costs set to zero:

| lookbacks | smooth | **gross** Sharpe | net Sharpe | turnover |
|---|---|---|---|---|
| (5,10,20) | 10 | **-0.32** | -0.50 | 115.6x |
| (5,10,20) | 20 | **-0.37** | -0.48 | 70.7x |
| (10,20,40) | 20 | **-0.50** | -0.58 | 48.7x |
| (20,40,80) | 20 | **-0.66** | -0.71 | 32.4x |

This is the important finding. The sleeve is not a good strategy ruined by
costs - it **loses money before costs, at every horizon tested, monotonically
worse as the horizon lengthens.** There is no mean-reversion premium in this
universe; the mirror image of a profitable trend signal is a losing signal, as
it must be.

So any benefit comes purely from **negative correlation**, not from the sleeve's
own return. A negative-expected-return hedge can raise portfolio Sharpe - that
is legitimate portfolio construction - but it is a far more fragile claim than
"we found a second source of return", and it must be labelled as such.

## Result 3 — portfolio effect, and a better design

| variant | full 2005-26 | held-out 2018-26 | turnover |
|---|---|---|---|
| trend only | 0.82 | 0.63 | 13.6x |
| blended, rev share 0.15 | 0.84 | **0.72** | 15.5x |
| blended, rev share 0.30 | 0.80 | 0.66 | 21.7x |
| regime dampener, floor 0.3 | **0.86** | **0.77** | 44.2x |
| regime dampener, floor 0.0 | 0.77 | 0.62 | 69.9x |

The **dampener** uses the same regime score but only scales trend exposure
*down* in chop - it never takes the opposite side. It wins on Sharpe in both
periods, which says the real value of the regime signal is **knowing when to
size down**, not knowing when to reverse. But it triples turnover (13.6x ->
44.2x, cost drag 0.27% -> 0.88% at 2bps), so it is materially more
cost-sensitive. The floor matters: switching off entirely in chop (floor 0.0)
is worse than doing nothing.

`blended rev 0.15` is the better risk-adjusted choice: it improves both periods
consistently for a 14% turnover increase.

## Verdict

**Mechanism confirmed, net benefit NOT established.** Cumulative trials are now
~24, putting the noise bar near 0.30. The held-out improvement of 0.63 -> 0.72
is 0.09 of Sharpe - within the range that selection effects can manufacture at
that trial count. Treat as **suggestive, not proven.**

Do not ship this as a return sleeve on the strength of these numbers. The
defensible conclusions are:
1. Regime measurement has value, and its value is in **position sizing**, not
   in reversing direction.
2. Any reversion allocation must be justified as a **hedge**, with its negative
   standalone expectancy stated explicitly.
3. This needs walk-forward validation, not another parameter split, before it
   earns a place in the live system.

---

# Addendum 2: the regime-conditional claim was LOOKAHEAD (2026-10-27)

## What was tested

The earlier addendum left open whether reversion gated *hard* on a lateral
regime works - holding it only in chop and going flat otherwise, rather than
blending it in at 15-30% weight. `signals/combine.py::gated_signal` implements
that. The attribution diagnostic that motivated it looked spectacular:

| trendiness quintile | reversion PnL | trend PnL |
|---|---|---|
| Q1 most lateral | **+1.85 bps/day** | -0.41 |
| Q3 | -0.10 | +0.34 |
| Q5 most trending | **-2.25 bps/day** | +0.67 |

Perfectly monotonic, mirror-image, with a date-clustered Sharpe of **5.49** on
the Q1 subset. A Sharpe of 5.49 on a daily liquid-ETF strategy does not exist,
which is what prompted the check rather than the celebration.

## The bug

The mask used `trendiness` at date **t** against the PnL earned **at date t**.
But the efficiency ratio and variance ratio at t are computed from prices
through t inclusive. Selecting "days where trendiness at t was low" therefore
conditions on the very return being measured: a low efficiency ratio *means*
the net move was small relative to the path, i.e. it directly selects days
whose return reverted. Circular.

Shifting the mask by a single day destroys the entire effect:

| mask lag | Q1 most lateral | Q3 | Q5 most trending | Q1 Sharpe |
|---|---|---|---|---|
| 0 (lookahead) | +1.85 | -0.10 | -2.25 | **5.49** |
| **1 (causal)** | **-0.07** | -0.19 | +0.03 | **0.05** |
| 2 (causal) | -0.00 | -0.09 | -0.09 | 0.04 |
| 3 | -0.09 | -0.13 | -0.18 | - |
| 5 | -0.11 | -0.21 | -0.16 | - |

Nothing survives. The monotonic pattern was entirely an artefact.

Note the `backtest.py` engine was never affected - its double shift means a
signal formed at t only meets the return at t+2. The causal gated backtest said
gross Sharpe **-0.00** all along, and it was right. The bug was in an ad-hoc
diagnostic written outside the tested engine, which is exactly where this class
of error survives.

## Portfolio-level result (causal, as it always was)

| variant | full 2005-26 | held-out 2018-26 | turnover |
|---|---|---|---|
| trend only | 0.82 | 0.63 | 13.6x |
| reversion gated <0.20 | -0.27 | -0.48 | 231.0x |
| reversion gated <0.30 | -0.45 | -0.35 | 278.7x |
| switch rev<0.20 / trend>=0.20 | 0.76 | 0.61 | 106.8x |
| trend gated >=0.20 | 0.73 | 0.60 | 98.3x |

Gross (zero-cost) Sharpe of gated reversion is **-0.00 to -0.07** - the problem
is not costs. Gating also destroys diversification: at gate<0.20 only **1.9 of
20** instruments are active on average and **25% of days have none at all**.

## Why the idea cannot work as stated

**A range is only identifiable after it has happened.** By the time an
efficiency ratio or variance ratio tells you the market has been rotational,
the rotation is in the past. Any measure of "we are in a range now" is built
from the prices that already rotated - which is precisely why removing the
lookahead removes the edge.

This is a stronger and more honest explanation than the earlier "reversion is
just the mirror of trend". The mirror argument is true but secondary; the
binding problem is that the conditioning variable is not available in time.

## Consequence for Addendum 1

The blended variant's held-out 0.63 -> 0.72 was causal (the engine's shifts
guarantee it), so it is not a bug. But with the mechanism now shown absent, it
should be read as **selection noise across ~24 trials**, not as evidence. The
earlier verdict of "suggestive, not proven" was right; it can now be closed as
**not supported**.

Trend-only remains the system. Cumulative trials ~60; noise bar ~0.34.
