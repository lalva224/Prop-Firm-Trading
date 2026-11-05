# FTMO Testing Harness: challenge + funded, free, locally

Built 2026-10-27. Tests both phases against FTMO's exact published rules on
21 years of free daily data. No fee, no demo account, no live risk.

```bash
.venv/bin/python scripts/run_ftmo.py --vol 0.12        # one config, 3 intraday models
.venv/bin/python scripts/run_ftmo.py --sweep-vol       # volatility sweep
.venv/bin/python scripts/run_ftmo.py --walk-forward    # does tuning help?
.venv/bin/python scripts/run_ftmo.py --walk-forward --model worst
python3 -m pytest tests/test_ftmo.py -q                # 9 rule tests
```

## Rules encoded (FTMO Challenge 2-Step, 2026)

| | |
|---|---|
| Phase 1 target | +10% |
| Phase 2 target | +5% |
| Max daily loss | **5% of the INITIAL balance**, from start-of-day, reset at midnight server time |
| Max overall loss | **10% of the INITIAL balance - static**, the floor never moves up |
| Min trading days | 4 per phase |
| Time limit | none (the old 30/60-day caps were removed) |
| Profit split | **80%**, rising to 90% on the Scaling Plan (3 consecutive payouts of 10%+) |
| First payout | 14 days after the first trade, then on demand |

### Three rules my earlier simulator got wrong

1. **Limits are fractions of the INITIAL balance, not current equity.** At +50%
   profit, a 5% move on current equity is 7.5% of initial and breaches. Coding
   the limit against current equity silently makes the account safer than it is.
   Test: `test_daily_limit_is_fraction_of_initial_balance_not_equity`.
2. **Limits are evaluated on EQUITY INCLUDING FLOATING P&L.** A position down 5%
   intraday breaches even if it closes flat. Close-only simulation cannot see
   this. Test: `test_intraday_floating_loss_breaches_even_if_close_is_flat`.
3. **The split is 80%, not 85%.**

## The intraday problem, and how it is bounded

Daily data cannot observe the intraday equity path, so the harness computes
three models and reports all of them:

| model | construction | role |
|---|---|---|
| `close` | close-to-close only | **optimistic** - understates breaches |
| `brownian` | portfolio as one diffusion; expected within-day minimum ≈ 0.8σ below the open. Accounts for diversification via the portfolio's own σ | **realistic** |
| `worst` | sum of every instrument's own High/Low excursion against its position | **pessimistic** - assumes all instruments bottom simultaneously |

The truth is between `close` and `worst`. **A configuration is only trustworthy
if all three models agree on it.**

## Result: intraday modelling reverses the earlier recommendation

EV per fee, ETF universe, 2005-2026:

| vol target | close | **brownian** | worst | agree? |
|---|---|---|---|---|
| 8% | 2.4x | 2.4x | 2.0x | yes |
| 10% | 4.9x | 4.6x | 3.9x | yes |
| **12%** | **6.0x** | **4.9x** | **4.8x** | **yes** |
| 15% | 6.0x | 5.8x | **1.2x** | **no** |
| 20% | 6.2x | 5.0x | **0.1x** | **no** |

At 20% volatility, daily-limit breaches rise from 17.6% (close) to **57.1%**
(worst). **The binding constraint is the 5% daily limit on floating equity, not
the 10% total limit** - and close-only simulation is blind to it.

**This reverses Addendum 1's "raise volatility to 20%".** That recommendation
was an artefact of simulating on closes. The robust setting is **12%** - the
production default - because it is the only one where the answer does not
depend on which intraday model is right.

## Walk-forward: is tuning worth doing at all?

`src/tcs/tuning.py` runs the honest protocol per fold: pick the best parameters
on TRAIN, score that choice on TEST, and compare against a fixed default on the
same TEST. 45-point grid (vol x buffer x smooth), 6 folds, 270 trials.

| scoring model | vol chosen per fold | tuning edge | folds won | verdict |
|---|---|---|---|---|
| close | 0.20 x6 | +0.0335 | 5/6 | TUNE |
| brownian | 0.15, 0.20 x5 | +0.0246 | 5/6 | TUNE |
| **worst** | 0.12, 0.12, 0.20, 0.15, 0.15, 0.15 | **-0.0038** | **2/6** | **DO NOT TUNE** |

**The verdict flips with the intraday model, and the chosen volatility disagrees
across models in 5 of 6 folds.** The parameter is not identifiable: "tuning
helps" is a statement about which simulator assumption you believe, not about
the market.

Also note `in_sample_inflation` ≈ **0.042-0.045** against a mean test score of
0.016-0.046 - **roughly half the apparent edge evaporates out of sample**, even
with a correct walk-forward protocol.

## Conclusion for tuning

**Set volatility to 12% and stop.** Not because 12% scored best - it did not -
but because it is the only setting whose EV survives every intraday assumption.

Use this harness to **verify robustness, not to maximise a score.** The
question it answers well is "does this configuration hold up under every
assumption I cannot rule out?" The question it answers badly is "what is the
best configuration?" - and 270 trials against one simulator is exactly how a
plausible number gets manufactured.

## What this harness cannot test

- **Payout denial.** The dominant real risk, and absent from every backtest.
- Live execution, slippage on FTMO's own feed, platform behaviour.
- The 2-minute news blackout.
- Whether FTMO US (OANDA) offers the instruments the universe assumes.
- Intraday path within a day beyond the three bounds above.

For those, the sequence is: this harness -> FTMO Free Trial (14 days, $0,
target cut to 5%, min days cut to 2 - tests execution, not outcome, and does
NOT convert to a funded account) -> one smallest-size real challenge ->
**withdraw once and confirm the money lands** -> only then scale.

Note no free trial covers the funded phase. **The simulator is the only free
way to test maintenance**, which is why it models both phases.
