# Asset Selection: the search destroys what it looks for

Tested 2026-10-27. **Negative result, and the most important methodological
finding in the project.**

## The question

"Find the best assets to trade." Done properly: 88 candidate instruments
(equity indices and regions, 20 sectors, 15 bond/credit, 16 commodities, 7 FX,
real assets, crypto), ranked by single-instrument trend Sharpe using **only
2005-2017**, then the selected universes validated on **2018-2026**, never
touched during selection.

## Result: performance-based selection fails out of sample

| universe | n | eff. bets | Sharpe **IS** | Sharpe **OOS** | CAGR OOS |
|---|---|---|---|---|---|
| top-10 by in-sample Sharpe | 10 | 3.4 | **1.10** | **0.47** | 5.43% |
| top-20 by in-sample Sharpe | 20 | - | 1.02 | 0.29 | 2.97% |
| top-30 by in-sample Sharpe | 30 | - | 0.99 | 0.15 | 1.15% |
| all 79 (no criterion) | 79 | 4.5 | 0.86 | **0.16** | 1.22% |
| top-3 per asset class | 18 | - | 1.10 | 0.37 | 4.06% |
| **our original 20 (structural)** | 20 | 5.2 | **0.82** | **0.66** | **7.65%** |
| structural expanded | 22 | 6.5 | 0.87 | 0.35 | 3.68% |

**Every selected universe beat the original in-sample (0.99-1.10 vs 0.82) and
lost to it out-of-sample (0.15-0.47 vs 0.66).** The selection bought in-sample
Sharpe with out-of-sample Sharpe, at roughly 1:1.

**More instruments does not help either.** All 79 gives OOS 0.16 - the added
sector and single-country ETFs are mostly correlated to SPY, so they dilute
without diversifying.

## Why ranking by performance picks the wrong things

The in-sample top 15 included **SHY (0.89), MBB (0.69), BND (0.53)** - three
US rate instruments. They ranked top because 2005-2017 contained one enormous
persistent downtrend in yields, which a trend follower rode beautifully. That
trend **reversed violently in 2022**, inside the held-out window.

Ranking by past performance systematically selects instruments that had one big
trend. One big trend in the ranking window is exactly what will not repeat.

## The statistical caveat that bounds all of this

Standard error of a Sharpe estimate is `sqrt((1 + SR²/2) / years)`:

| window | years | SE at SR=0.5 | 95% interval for a true SR of 0.50 |
|---|---|---|---|
| held-out 2018-26 | 8.8 | **0.36** | -0.20 to +1.20 |
| full sample 2005-26 | 21.8 | 0.23 | +0.05 to +0.95 |
| to reach SE = 0.10 | **112** | 0.10 | +0.30 to +0.70 |

Applied to the universes above, every 95% interval overlaps every other:

| universe | OOS Sharpe | 95% interval |
|---|---|---|
| top-10 by performance | 0.47 | -0.23 to +1.17 |
| all 79 | 0.16 | -0.50 to +0.82 |
| original 20 structural | 0.66 | -0.07 to +1.39 |
| structural expanded | 0.35 | -0.33 to +1.03 |

**I cannot distinguish these universes from each other with the data that
exists.** The original's 0.66 may itself be luck. 112 years would be needed to
pin a Sharpe to ±0.10.

What IS robust is the **paired** comparison: the same selection method, applied
to the same data, scored higher in-sample and lower out-of-sample. That
direction is consistent across every cut (top-10, 20, 30, 45, class-balanced)
and is not a one-sample fluke.

## The answer to "find the best assets"

**It cannot be done from return data.** The search is the problem: with
~9 years of validation data and a 0.36 standard error, any ranking you build is
mostly noise, and acting on it costs you real out-of-sample performance.

### What you CAN optimise on

Facts, not estimates:

1. **Liquidity** - measurable today, not an estimate. Pick the most liquid
   instrument representing each factor.
2. **Cost** - spread, commission, financing. Directly measurable.
3. **Structural distinctness** - does this instrument represent a risk factor
   nothing else in the book represents? Answerable by reasoning, not backtest.
4. **Instrument integrity** - avoid structural-decay instruments (UNG, USO,
   VXX). A trend system will sit short them permanently and show a wonderful
   backtest; that return is contango carry with blow-up risk, not trend.

### The rule

Choose **15-25 liquid instruments spanning genuinely distinct risk factors,
decided before looking at a single backtest result, and then do not touch the
list.** Our original 20 works precisely because it was chosen that way.

Any later change to the universe must be justified structurally ("this factor
is unrepresented") and never by performance ("this one backtests better").
