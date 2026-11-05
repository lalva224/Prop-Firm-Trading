# Order-Flow Bot: Stack, Costs, and Phase Plan

Research notes. Pricing checked **2026-10-27** via vendor sites and reviews — **re-verify before committing money**, these numbers move.

---

## 1. The Jigsaw constraint

Jigsaw daytradr **cannot be automated**. No API, no scripting language, no strategy engine — it is deliberately manual/discretionary. The furthest it goes:

- **Alerts**: iceberg, block trade, large trade, divergence
- **Semi-auto execution**: one-click, auto order types, volume-based stops, automated exit strategies

Its bridge plugs into NinjaTrader 7/8, MetaTrader 5, and MultiCharts.NET — so if you want Jigsaw's visuals and automation on the same feed/account, that is the only route, and the automation lives in NinjaScript/C#, not in Jigsaw.

**Conclusion:** Jigsaw is a *research and validation lens*, not a bot host. We reproduce its transforms on a stack that can place orders.

### What Jigsaw is still worth using for

1. **Edge discovery via Market Replay** — eyeball whether a pattern actually precedes the move, before coding it.
2. **Labeling ground truth** — watch N instances in replay, timestamp them, validate your detector against that set.
3. **Live supervision** — run it beside the bot on the same feed as a human sanity monitor.

Note it is a **Phase 1** tool (futures/stocks feeds only). In Phase 0 (crypto) the validator is your own plotting.

---

## 2. Required layers

### Data
Order flow needs trades **with aggressor side** plus **book depth** — bars are useless.

| Source | Notes |
|---|---|
| **Databento** | CME MBO/MBP-10, historical + live, clean Python API. Best research substrate. History from 2010-06-06. |
| Rithmic R\|API | protobuf, C++/.NET; `async_rithmic` for Python |
| CQG | FIX / Web API |
| Tradovate | plain REST + WebSocket — easiest retail futures API to write against |
| IBKR | `ib_async`; **snapshot-based ~250ms conflated ticks**, not true tick-by-tick |
| **Binance / Bybit** | Free full depth + aggressor-tagged trades. Phase 0 substrate. |

### Features (the Jigsaw reimplementation) — each ~50-150 lines
- Cumulative delta (aggressive buy vol - sell vol)
- **Reconstructed tape** — group consecutive same-aggressor prints within a few-ms window to recover one large order split across prices
- **Iceberg detection** — executed volume at a price >> displayed size there, level does not lift
- **Absorption** — large aggressive volume, no price progress
- **Stacking / pulling** — diff successive L2 snapshots per level
- Book imbalance (top-N bid/ask ratio + rate of change)
- Volume ladder / price-level profile

### Backtest
Order-flow strategies cannot be backtested on bars. **`nautilus_trader`** — Rust core, Python API, native L3/MBO book support, Databento adapter built in. The alternative (own event loop over MBO replay) is a month you do not need to spend.

### Execution
`nautilus_trader` is the chosen engine. **Adapter availability is the binding constraint** — see §3.

Alternatives if a GUI-native path is ever wanted: **ATAS** / **Quantower** (order-flow platforms *with* C# strategy APIs — effectively "Jigsaw, but automatable"), **Bookmap** (Java addon API), **NinjaTrader 8** (NinjaScript).

---

## 3. Cost breakdown

### Phase 0 — crypto: **$0/month**
Binance/Bybit publish full-depth book + aggressor-tagged trades free. nautilus has first-class adapters for **both data and execution**. Nothing to buy. VPS optional later (~$10-50/mo).

### Phase 1 — futures research data (Databento)

| Tier | Price | Notes |
|---|---|---|
| Usage-based | from **$0.50/GB**, historical only | No monthly minimum, pay what you pull |
| Standard | **$199/mo** | Live + historical, license fees included |
| Plus | $1,750/mo + license fees | Annual contract |
| Unlimited | $4,500/mo + license fees | Annual contract |

**Schema choice is the cost trap.** MBO is by far the heaviest — an active contract-day runs into GBs, so $0.50/GB stops being cheap fast. MBP-10 is an order of magnitude smaller and sufficient for delta, absorption, stacking/pulling, and reconstructed tape. Go MBO only where order-level attribution is genuinely needed (true iceberg identity, queue position). **Run their pricing calculator before any bulk download.**

### Phase 2 — execution venue

| Route | Monthly | Commission | nautilus adapter |
|---|---|---|---|
| **Interactive Brokers** | $0 API | varies | **first-class** (Rust v2 path) |
| Rithmic | $25 + **$100 R\|API** | $0.10/side routing | **RFC only** (issue #3768) |
| Tradovate | $0 / $99 / $1,499 lifetime | $1.29 / $0.99 / $0.59 per side standard | **none** |

Tradovate micros: $0.39 / $0.29 / $0.09 per side by tier. Monthly plan break-even ~495 micro round-turns/month.

**The $100/mo Rithmic API fee is the smaller cost. The bigger one is that neither Rithmic nor Tradovate has a production nautilus adapter** — you would write it yourself (Rithmic protobuf = hard, Tradovate REST+WS = easier), weeks before the first live order. IB is the only futures venue nautilus already drives end to end.

### CME exchange fees — unavoidable, on top of everything (effective 2026-06-01)

| | Per exchange | All 4 bundled |
|---|---|---|
| Non-pro **depth of market** | **$12.10/mo** | **$36.50/mo** |
| Non-pro top of book | $1.55/mo | $4.65/mo |

Top-of-book is useless for order flow — depth is mandatory. Professional rates run ~$124/exchange/mo. **Staying classified non-professional** (own money, unregistered, no redistribution) is worth more than every other line item combined.

### Jigsaw (optional, research only)
- Lifetime: **$579** Independent / $879 Professional / $1,979 Institutional
- **+$50/mo or $500/yr only when trading live**; SIM free forever

Since it cannot automate anything, keep it permanently on free SIM as a replay/validation lens and never pay the subscription. $579 one-time, or skip entirely.

---

## 4. Decided plan

| Phase | Spend | Content |
|---|---|---|
| **0** | $0 | Crypto. Feature library + nautilus pipeline + determinism. Validator = own plots. |
| **1** | tens of $ one-off | Databento usage-based, MBP-10, selected contract-months. Jigsaw free SIM for visual ground truth. |
| **2** | ~$12-15/mo | IBKR execution + $12.10/mo CME depth, no API fee. |

**Caveat on the cheap Phase 2:** IB data is snapshot-based (~250ms conflated), fine for execution and slower order-flow setups, but it will not reproduce Jigsaw's tape. If the edge needs real tick granularity live, add Databento Standard ($199/mo) for the **signal** and keep IB purely for **execution**. Decide after Phase 1 reveals the required resolution — do not pre-buy.

**Skip Rithmic and Tradovate** until there is a proven edge plus a specific latency or fill-quality reason. Paying $125/mo and then writing your own adapter is the worst available opening move.

---

## 5. Sources

- Jigsaw: [platform](https://www.jigsawtrading.com/trading-software/), [connectivity](https://www.jigsawtrading.com/blog/daytradr-connectivity-explained/), [review/pricing](https://tradingtoolshub.com/review/jigsaw-trading/)
- Databento: [pricing](https://databento.com/pricing), [usage pricing FAQ](https://databento.com/docs/faqs/usage-pricing-and-data-credits)
- Rithmic: [R|API at AMP](https://www.ampfutures.com/trading-platform/rithmic-r-api), [AMP data fees](https://www.ampfutures.com/trading-info/exchange-data-fees), [Optimus fees](https://optimusfutures.com/Futures-Trading-Fees.php)
- Tradovate: [pricing 2026](https://tradingtoolshub.com/blog/tradovate-pricing-guide-2026/)
- CME: [January 2026 fee list](https://www.cmegroup.com/market-data/files/january-2026-market-data-fee-list.pdf)
- nautilus_trader: [repo](https://github.com/nautechsystems/nautilus_trader), [IB adapter](https://nautilustrader.io/docs/nightly/integrations/ib/), [Rithmic RFC](https://github.com/nautechsystems/nautilus_trader/issues/3768)
