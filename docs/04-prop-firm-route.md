# Prop-Firm Route: VIABLE, with conditions

Analysed 2026-10-27. **This is the most promising path found so far** for
turning a modest Sharpe into meaningful absolute return.

## Why it works: a challenge is a call option

You pay a fixed fee for the right to earn a profit share on capital you do not
own, with a knockout at the drawdown limit. Downside capped at the fee, upside
leveraged. That payoff is structurally different from trading own capital, and
it can be positive-EV with a strategy whose Sharpe is far too low to be
interesting on its own.

**The prop route does not improve the strategy. It multiplies the capital base.**

## Measured on OUR trend system (real returns, not simulated)

All overlapping 252-day windows, 2005-2026, stepped every 5 days:

| vol target | rules | pass | fail | timeout |
|---|---|---|---|---|
| 8% | FTMO: +10% / -10% static | 51.6% | 2.9% | 45.5% |
| 12% | FTMO: +10% / -10% static | 69.1% | 4.6% | 26.3% |
| **20%** | **FTMO: +10% / -10% static** | **76.1%** | **15.7%** | 8.2% |
| 30% | FTMO: +10% / -10% static | 68.2% | 30.8% | 1.0% |
| 8% | Futures: +6% / -4% trailing | 44.9% | 51.5% | 3.6% |
| 12% | Futures: +6% / -4% trailing | 42.0% | 57.9% | 0.1% |
| 20% | Futures: +6% / -4% trailing | 42.2% | 57.8% | 0.0% |

### Two findings that matter

**1. TRAILING drawdown is the killer, not the drawdown size.** A 4% trailing
limit pins the pass rate at ~42% no matter what volatility you run, because the
limit tracks every new equity high. A 10% *static* limit lets the strategy
breathe. **Prefer static-drawdown firms; avoid trailing-drawdown futures firms
(Apex/Topstep style) for this approach.**

**2. The optimal volatility is set by the RULES, not by preference.** Under
static DD the sweet spot is ~20% vol: below it you time out (45% of windows at
8% vol never reach +10%), above it you breach. Our production default of 12% is
tuned for own-capital survivability and is *wrong* for a challenge.

### Real data beat the Gaussian model

A Monte Carlo at the same Sharpe/vol predicted 63.6%; the real return series
gave **75.3%**. Trend following's drawdowns develop slowly enough that the
target is often reached first - a Gaussian random walk understates it.

## Why the industry pass rate is 5-14% and mine is 76%

Both can be true. The gap is behaviour and rules, not market:

- Real traders over-leverage, revenge-trade and size up after losses. A bot does not.
- Two-phase challenges: Phase 1 (+10%) then Phase 2 (+5%). Sequential, so ~0.76 x 0.85 = **~65%**.
- Minimum trading days, time limits, news blackouts.
- **Consistency rules** (no single day above ~30-40% of total profit).
- Only **~7% of all challenge buyers ever receive a payout** (vs 12.3% average pass rate).

My 76% is also optimistic because the windows overlap heavily - 1023 windows
but only ~21 independent years - and because the trend parameters were selected
on this same data (held-out Sharpe was 0.63, not 0.82).

## The incompatibility to watch

**Trend following earns lumpily** - most of its profit arrives in a few crisis
months (+19.6% in 2008, +21.5% in 2022). **Consistency rules are specifically
hostile to that profile.** A firm that caps any single day at 30% of total
profit may deny payout precisely when the strategy works. Read this rule before
choosing a firm; it matters more than the profit split.

## Rules as of 2026

Bots are **allowed** at most major firms (FTMO, FundingPips, The5ers, FXIFY)
with conditions. Prohibited: HFT, latency arbitrage, tick scalping on stale
quotes, grid/unhedged martingale, and public commercial EAs. A custom private
algo is fine. Copy-trading between **your own** accounts is generally allowed;
account-to-account across different owners is universally banned. FTMO caps
**$400k per strategy** across combined accounts, which bounds scaling.

## The real risk is counterparty, not market

The industry earned an estimated **$850M in 2026** across **12 million
challenge purchases**. The business model is challenge fees. Payout refusal is
a documented practice. Mitigation: diversify across **firms**, not just
accounts; withdraw early and often; never treat a funded balance as your asset.

## Plan

1. Run the trend system at **~20% vol**, not 12%.
2. Choose **static-drawdown** firms only.
3. Screen for **consistency rules** that would penalise lumpy profits.
4. Verify the automation policy in writing for the specific firm.
5. Spread across several firms; withdraw at the first eligible date.
6. Treat each fee as an option premium - expect to lose some outright.

## Open question

Our strategy is slow (daily rebalance, weeks-to-months holding). Challenge time
limits and minimum-trading-day rules may conflict. Needs checking per firm
before any fee is paid.

---

# Addendum: rule simulator + firm shortlist (2026-10-27)

`src/tcs/prop.py` encodes each firm's published rules and walks the strategy's
real return series through challenge phases *and* the funded account, over every
historical window. **This is the free test** - no fee, no demo, 21 years of data.

## Challenge + 2 years funded, by firm and volatility

| firm | vol | pass | breach | timeout | mean payout | EV | EV/fee |
|---|---|---|---|---|---|---|---|
| **The5ers High Stakes 2-Step** | **20%** | 59.4% | 32.8% | 7.8% | 10.56% | **9.36%** | **7.8x** |
| **FTMO 2-Step** | **20%** | 54.9% | 36.2% | 8.9% | 10.06% | **8.86%** | **7.4x** |
| The5ers High Stakes | 15% | 63.7% | 21.3% | 15.1% | 9.44% | 8.24% | 6.9x |
| **E8 Pro** | **15%** | **68.7%** | 21.1% | 10.2% | 9.39% | 8.19% | 6.8x |
| FTMO 2-Step | 15% | 56.1% | 23.3% | 20.6% | 8.89% | 7.69% | 6.4x |
| E8 Pro | 10% | 63.8% | 9.8% | 26.5% | 7.08% | 5.88% | 4.9x |
| FTMO 2-Step | 10% | 47.4% | 12.8% | 39.8% | 6.44% | 5.24% | 4.4x |
| E8 One *(trailing)* | 10% | 52.9% | 33.3% | 13.8% | 3.94% | 2.74% | 2.3x |
| E8 One *(trailing)* | 15% | 43.8% | 56.0% | 0.2% | 2.38% | 1.18% | 1.0x |
| Futures firm *(EOD trailing)* | 15% | 37.7% | 62.3% | 0.0% | 1.10% | 0.30% | 0.4x |
| Futures firm *(EOD trailing)* | 20% | 31.1% | 68.9% | 0.0% | 0.46% | **-0.34%** | -0.4x |

### Confirmed: drawdown TYPE dominates everything

Static-drawdown firms return **4.4x to 7.8x the fee**. Trailing-drawdown firms
return **0.1x to 2.3x, and go negative** at higher volatility. Same strategy,
same fee, same market. The rule structure is the whole game.

### Optimal volatility is 15-20%

Below that you time out (FTMO: 39.8% of windows never reach +10% at 10% vol).
Above it you breach (61.2% at 30% vol). Our production default of 12% is tuned
for own-capital survivability and is wrong for this use.

Note E8 Pro peaks at a *lower* volatility (15%) than FTMO/The5ers (20%) because
its drawdown is 8% rather than 10%. Tighter floor, less room to run.

## Funded phase: payout over 2 years (FTMO rules)

| vol | withdraw weekly | monthly | quarterly | **never** | survives |
|---|---|---|---|---|---|
| 8% | 14.94% | 14.45% | 13.68% | **0.00%** | 97.5% |
| 10% | 18.09% | 17.32% | 16.39% | **0.00%** | 95.7% |
| **15%** | 20.15% | **20.79%** | 20.07% | **0.00%** | 92.2% |
| 20% | 17.94% | 19.84% | 19.44% | **0.00%** | 81.9% |
| 30% | 11.95% | 9.70% | 6.22% | **0.00%** | 63.2% |

**Correction to the earlier prediction.** I expected the funded phase to favour
*lower* volatility than the challenge. It does not - the optimum is ~15% in
both. What actually changes is survival: 92.2% at 15% vol collapses to 63.2% at
30%.

**Withdrawal frequency barely matters (20.1 / 20.8 / 20.1) but withdrawing at
all is everything.** The "never" column is 0.00% by construction - unwithdrawn
profit is not yours, it is collateral the knockout can still reclaim.

## Firm shortlist

| rank | firm | why |
|---|---|---|
| 1 | **FTMO Challenge 2-Step** | 10% static DD, **no formal consistency rule**, no time limit, largest/most established |
| 2 | **E8 Pro** | 8% static DD, **no consistency rule**, single phase (faster to funded) |
| 3 | **The5ers High Stakes 2-Step** | 10% static DD, no consistency rule, best measured EV |
| - | FXIFY, Maven | also no consistency rule; rules not yet encoded |
| avoid | FTMO 1-Step | 10% **trailing** EOD |
| avoid | E8 One / E8 Signature | 6% and 3-4% **trailing**, plus 40%/35% consistency |
| avoid | Apex, Topstep, most futures firms | EOD trailing (7 of top 10 futures firms) |

## Caveats on these numbers

1. **In-sample.** Trend parameters were selected on this data; held-out Sharpe
   was 0.63 vs 0.82. Real pass rates will be lower.
2. **Overlapping windows.** ~1000 windows but only ~21 independent years, so
   confidence intervals are wide.
3. **The funded model is optimistic**: it assumes withdrawals every 21 days
   reset equity cleanly. Real firms add minimum profitable days, payout
   schedules and buffers. Verify per firm.
4. **Not modelled at all**: payout denial, mid-stream rule changes, the firm's
   own feed/slippage, news blackouts (E8 and FTMO both impose a 2-minute
   window around major releases).

## Free ways to test, in order of value

1. **This simulator** - $0, exact rules, 21 years, instant. Already built.
2. **FTMO Free Trial** - 14 days, no fee; target cut 10% -> 5%, min trading
   days 4 -> 2. Does **not** convert to a funded account.
3. **FundingPips free trial** - tests both phases of selected models.
4. **FundedNext** - single-stage practice trial.
5. Broker demo on the same instruments, running the live bot end-to-end.

---

# Addendum 2: counterparty due diligence (2026-10-27)

The EV numbers above are worthless if the firm does not pay. Reputation research
**changes the shortlist**.

## Industry context: this is not a stable sector

**80 to 100 prop firms shut down between February 2024 and early 2026** - the
largest collapse in the industry's history. Documented casualties:

| firm | what happened |
|---|---|
| The Funded Trader | Paused 2024-03-28; admitted **$2M+ in denied payouts** |
| True Forex Funds | Shut 2024-05-13; ~300 traders, **$1.2M unpaid** |
| SurgeTrader | Shut 2024-05-24 |
| The Funded Engineer, Stocknet, Skilled Funded Trader | closed |
| MyForexFunds | CFTC charged 2023-08 ($310M in fees from 135,000 customers). **The case collapsed in May 2025** - dismissed with prejudice for prosecutorial misconduct, CFTC sanctioned $3M+. Fraud was never proven. |

Root cause per industry analysis: the failures were **newer entrants that scaled
marketing before building the infrastructure to support profitable traders.**

## Reputation data

| firm | Trustpilot | reviews | payouts | verdict |
|---|---|---|---|---|
| **FTMO** | **4.8★** | 40,000+ | **$500M+ verified** | Since 2015. No major payout scandals, no retroactive rule changes, no withdrawal freezes. **Clear #1.** |
| **The5ers** | **4.8★** | 21,000+ | 260,000+ traders funded | Strong, long-standing. |
| FundingPips | 4.5★ | - | $110M+ | Acceptable. |
| **E8 Markets** | **4.3★ and "Falling"** | - | $76M claimed | **DOWNGRADE - see below.** |

### E8 is downgraded despite having the best rules

E8 Pro's rule set is genuinely good (8% static, no consistency rule) and it
measured 6.8x EV. But the complaint pattern is specifically about **payouts**:

- Traders reporting **$40,000 and $54,200 requested and never paid**
- **Partial payouts** (20% instead of the full amount)
- Unexplained account deactivations
- Post-evaluation restrictions **not present in the original contract**

Some traders do report fast 24-72h payouts. The picture is genuinely mixed -
not a confirmed scam, but discretionary payout behaviour. **Good rules on paper
are worth nothing when the payout decision is discretionary.** Rules are
contractual; payout practice is what the firm actually does.

## Revised shortlist

| rank | firm | rules | reputation | EV |
|---|---|---|---|---|
| **1** | **FTMO 2-Step** | 10% static, no consistency | **4.8★, $500M+, 10 yrs** | 7.4x |
| **2** | **The5ers High Stakes** | 10% static, no consistency | **4.8★, 21k reviews** | 7.8x |
| 3 | FundingPips 2-Step | 10% static | 4.5★, $110M | not yet measured |
| ~~2~~ | ~~E8 Pro~~ | best on paper | **4.3★ falling, payout complaints** | 6.8x, **discount heavily** |

**Weight reputation above EV.** The spread between 7.4x and 6.8x EV is noise
compared to the difference between a firm that pays and one that argues.

## Live regulatory risk - and the timing is immediate

- **The CFTC opened a public consultation on 2026-08-01 that closes 2026-11-30**
  on whether challenge-based prop firms fall under its authority. It explicitly
  considers whether **challenge fees constitute commodity-pool participation
  interests.** If so, formal rulemaking is expected early 2027, requiring CFTC
  and NFA registration.
- The **SEC has filed enforcement actions against two prop firms** for
  misrepresenting simulated-trading conditions.
- The **NFA issued Notice I-26-12** on affiliate marketing standards.

That consultation closes roughly five weeks from today. Anyone deploying capital
into challenges now is doing so into an unresolved regulatory question.

## The structural risk nobody advertises

**The business model requires most traders to fail.** If the bot genuinely
works, you stop being a customer and become a cost centre. Every firm's
incentive at that point is to find a rule you broke.

FTMO's size and ten-year record is precisely the defence: they can afford
winners, and they have a decade of not denying payouts. A firm with $76M paid
and a falling rating cannot absorb the same pressure.

## Protection protocol

1. **Firms operating 3+ years with verifiable payouts at scale.** No exceptions.
2. **Test the payout pipeline, not just the strategy.** Pass ONE challenge at the
   smallest size, withdraw once, confirm the money lands. Only then scale.
   Testing the strategy is free; testing the counterparty is not.
3. **Withdraw at the first eligible date, every time.** Never let a balance build.
4. **Archive the rules as of purchase date** - screenshot everything. Retroactive
   rule reinterpretation is the documented failure mode.
5. **Diversify across firms**, not just accounts.
6. **Treat every fee as fully expendable.** It is an option premium, not an asset.

---

# Addendum 3: the tradeable universe guts the edge (2026-10-27)

**No prop firm offers ETFs.** Every EV number above was measured on a 20-ETF
universe that cannot be traded on any prop platform. Rebuilt on what actually
is tradeable (`universe.PROP_TRADEABLE` / `PROP_PLUS_BONDS`): FX majors, index
CFDs, metals, energy, crypto, and bond futures.

## Sharpe collapse

| universe | n | costs | Sharpe full | **Sharpe OOS** | CAGR OOS |
|---|---|---|---|---|---|
| ETF *(not tradeable)* | 20 | 5bps | 0.79 | **0.60** | 7.05% |
| prop + bonds | 24 | 5bps | 0.41 | **0.26** | 2.58% |
| prop (no bonds) | 21 | 5bps | 0.32 | **0.10** | 0.40% |

Not a diversification problem - the prop universe is *better* diversified
(**7.9 effective independent bets** vs 5.2 for the ETFs, mean correlation 0.132
vs 0.176). The per-instrument signal is simply weaker.

## Where the signal died: sleeve decomposition

| sleeve | PROP universe | ETF universe |
|---|---|---|
| | full / OOS | full / OOS |
| **FX** | **-0.06 / -0.31** | **+0.57 / +0.47** |
| Equity | 0.24 / 0.04 | 0.25 / -0.01 |
| Commodity | 0.35 / 0.47 | 0.56 / 0.69 |
| **Bond** | 0.19 / 0.32 | **0.82 / 0.87** |
| Crypto | 0.66 / 0.37 | - |

Two findings:

**1. The ETF bond sleeve was carrying the entire system** at Sharpe 0.82/0.87 -
the best sleeve by a wide margin. TLT/IEF/SHY/LQD/HYG trend following is not
replicable with ZN/ZB/ZF bond futures (0.19/0.32), partly the roll artefact
again, partly because the credit ETFs (LQD/HYG) are a genuinely different
exposure.

**2. Spot FX trend is dead (-0.31 OOS) while FX-ETF trend works (+0.47).** The
reason is instructive: UUP/FXE/FXY embed interest-rate differentials (carry);
spot pairs like `EURUSD=X` are pure spot with no carry at all.

## Two measurement biases, both understating the result

| bias | size | direction |
|---|---|---|
| Cash index instead of total return | **-1.98%/yr** (SPY 10.89% vs ^GSPC 8.91%) | understates |
| Spot FX with no swap/carry | not quantified | understates |

A real index CFD **does** receive dividend adjustment on long positions, and a
real FX CFD **does** pay/receive swap. Both biases are artefacts of the proxy
data, not properties of the instruments. The true figure is above the measured
one - but these are offset by in-sample parameter selection and overlapping
windows, which push the other way.

## FTMO challenge EV on the real universe (5bps CFD costs)

| universe | vol | pass | breach | timeout | EV | EV/fee |
|---|---|---|---|---|---|---|
| ETF *(reference, untradeable)* | 20% | 54.1% | 37.4% | 8.5% | 8.26% | **6.9x** |
| prop: bonds + FX | 15% | 34.1% | 53.3% | 12.6% | 1.94% | 1.6x |
| prop: no FX | 15% | 33.7% | 54.9% | 11.4% | 3.50% | 2.9x |
| **prop: no FX, no bonds** | **20%** | **36.8%** | 61.6% | 1.6% | **4.62%** | **3.9x** |
| prop: no FX, no bonds | 15% | 38.1% | 51.0% | 10.9% | 4.14% | 3.4x |

**EV falls from 6.9x the fee to 3.9x. Pass rate falls from 54% to 37%.**

Still positive, but the margin is roughly half of what the ETF measurement
promised - and that 3.9x is in-sample.

Note dropping bond futures *improved* the result (3.9x vs 2.4x). ZN/ZB/ZF are
heavily correlated with each other and carry roll artefacts; they add
correlated risk without adding signal.

## Best configuration found

**14 instruments: index CFDs + metals + energy + crypto, at 20% volatility,
no FX, no bond futures.** 36.8% pass, EV 4.62% of account per attempt.

## Open items before any fee is paid

1. **Verify the FTMO US (OANDA) instrument list.** The whole configuration
   assumes index CFDs, metals, energy and crypto CFDs are all available on the
   US route. Unconfirmed.
2. **Correct the dividend and swap biases** - re-measure with total-return
   index proxies and FX carry included, to find the true Sharpe rather than the
   proxy-depressed one.
3. **US state check**: Arkansas, Delaware, Louisiana, Montana, South Carolina
   are excluded from FTMO US.
4. US payout requires a **TIN/SSN, IRS W-9, and a US bank account at a fully
   licensed institution** - Wise and Chime are explicitly not accepted.
