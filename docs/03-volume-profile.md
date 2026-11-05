# Volume Profile / Value-Area Reversion — RESULT: NO EDGE ON CRYPTO

Built 2026-10-27. **Negative result.** Keeping the code and the finding.

## The strategy

Auction Market Theory (Steidlmayer). The market alternates between **balance**
(rotational) and **imbalance** (trend day). In balance, the extremes of the
Value Area attract responsive orders and the **POC** acts as a magnet.

Trade: responsive **short at VAH**, responsive **long at VAL**, target **POC**,
only when a balance gate says the prior session was rotational. Known as
*value-area reversion* or *VA fade to POC*; the formalised version is the
Market Profile **"80% rule"**.

Implementation: `src/tcs/vp/` — `profile.py` (POC/VAH/VAL), `data_crypto.py`
(free Binance monthly archives), `backtest.py` (trade-level engine).

Reference profile is the **prior** session's, so nothing about a session leaks
into its own levels. Signals fire on bar close, fill at next bar open. If one
bar contains both stop and target, the **stop** is assumed first.

## The bug that faked an edge

The first run reported **held-out Sharpe 2.49, profit factor 1.54**. It was an
artefact, caught by two internal contradictions:

- 664 of 821 trades exited at "stop", yet stop exits averaged **-4.9 bps** —
  a stop exit cannot be profitable.
- 81% of trades hit their stop, yet hit rate read 49%.

Cause: price can open far above the **prior** session's VAH, so a short filled
at the next bar's open sat *above its own stop*. "Stopping out" then booked a
profit. Fix (**rule 6**): reject any fill already past its own stop or target —
the trade is simply not available at that price. `tests/test_vp_causality.py`
now asserts no stop exit is ever profitable.

A second error: `cost_ticks` is the wrong cost model for crypto. One tick on
BTC at $60k is 0.002 bps; real Binance perp taker fees are ~4.5 bps **per
side**. Added `cost_bps`.

## Result after both fixes

Expectancy in bps per trade, BTCUSDT and ETHUSDT, 1m bars 2022-01 to 2026-09:

| symbol | period | stop xATR | 0 bps | 2 bps | **9 bps (real)** |
|---|---|---|---|---|---|
| BTC | 2022-2024 | 0.10 | +0.41 | -1.59 | **-8.59** |
| BTC | 2022-2024 | 0.40 | -1.52 | -3.52 | **-10.52** |
| BTC | 2025-2026 | 0.10 | -0.68 | -2.68 | **-9.68** |
| BTC | 2025-2026 | 0.40 | -3.28 | -5.28 | **-12.28** |
| ETH | 2022-2024 | 0.10 | +2.90 | +0.90 | **-6.10** |
| ETH | 2022-2024 | 0.40 | -1.33 | -3.33 | **-10.33** |
| ETH | 2025-2026 | 0.10 | -1.75 | -3.75 | **-10.75** |
| ETH | 2025-2026 | 0.40 | +2.90 | +0.90 | **-6.10** |

**Negative in all 8 cells at realistic costs.** Even gross of all costs, only
2 of 8 are positive, and the best stop width flips between symbols and periods
(BTC prefers 0.10, ETH held-out prefers 0.40). That instability is the
signature of noise, not of an edge.

### Why it is *so* precisely marginal

At stop 0.10 xATR the trade structure is hit rate **22.1%** with reward:risk
**3.33**. Break-even reward:risk at a 22.1% hit rate is (1-0.221)/0.221 =
**3.55**. The strategy earns 3.33 against a 3.55 requirement.

The market is pricing this trade almost exactly fairly. Tighter stops raise
reward:risk and lower hit rate in near-perfect compensation across the whole
sweep — which is what an efficiently-priced structure looks like.

## What this does NOT prove

**Crypto has no session structure, and Market Profile depends on it entirely.**
The method was developed on CBOT futures with a real RTH open, close, and
single daily auction. A UTC-midnight boundary on a 24/7 perp is arbitrary:
there is no open, no close, no settlement, no overnight inventory to resolve.
The POC is still computable, but the *auction* the theory describes is absent.

So this is evidence against VA reversion **on crypto**, and only weak evidence
about the same trade on **ES with proper RTH sessions**. That remains untested.

Also untuned: the balance gate (`balance_max_range_atr=1.2`,
`balance_min_va_frac=0.35`), developing-POC vs prior-POC as the target, and
time-of-day filters.

## Trial count

~48 cumulative variants across the project. The noise bar is now near 0.33
Sharpe. Nothing here approaches it.

## Verdict and next step

**Do not keep tuning this on crypto.** The parameter instability plus the
near-exact break-even structure say there is nothing to find, and further
sweeps on the same data would only manufacture a fit.

The one informative next test is **ES futures with RTH sessions** — where the
method originated and where session boundaries mean something. Needs 1-minute
or `trades`-schema CME data: tens of dollars, one-off. That is a genuine test.
Running it would also resolve whether the balance gate is doing any work, since
futures have real trend-day vs balance-day structure.
