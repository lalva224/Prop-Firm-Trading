# trading

Systematic trading research. Two tracks, deliberately separated.

## Track 1 — Trend + carry (active)

Slow, diversified, free-daily-data strategies whose edge is risk premium rather
than speed. This is where profitability is actually plausible for a retail
account.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_backtest.py --universe etf
python3 -m pytest tests/ -q
```

Current held-out result: **Sharpe 0.63, ~7.5% CAGR at 12.7% vol** (2018-2026,
ETF universe). See [docs/02-trend-carry-design.md](docs/02-trend-carry-design.md)
for full results, the turnover bug that was fixed, validation, and the
unresolved futures-data discrepancy.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill it in; .env is gitignored
python3 scripts/check_env.py  # verifies parsing, gitignore, platform support
```

**Linux note:** the official `MetaTrader5` Python package is Windows-only.
On Linux choose **cTrader** when creating the FTMO account (official
cross-platform Open API), or run MT5 under Wine, or use a Windows VPS.

## Track 1b — Prop-firm route (active)

Turning a modest Sharpe into absolute return by trading firm capital. Challenge
and funded phases both testable locally against FTMO's exact rules, for free.

```bash
.venv/bin/python scripts/run_ftmo.py --vol 0.12        # 3 intraday models
.venv/bin/python scripts/run_ftmo.py --sweep-vol
.venv/bin/python scripts/run_ftmo.py --walk-forward --model worst
```

Robust setting: **12% volatility** — the only one whose EV survives every
intraday assumption. See [docs/04](docs/04-prop-firm-route.md) for firm
selection and counterparty risk, [docs/06](docs/06-ftmo-testing-harness.md) for
the harness and why tuning past 12% is not identifiable.

## Running MT5 on Linux — no Wine

MetaQuotes ships no native Linux build, so every Linux "MT5" is Wine underneath.
Instead, run **native Windows MT5 in a KVM VM** on the always-on mini PC:
an internal VPS, with snapshots.

```bash
bash scripts/vm/00-host-setup.sh
bash scripts/vm/01-create-vm.sh --windows-iso ~/iso/Win11.iso
python3 scripts/preflight.py --bridge-dir ~/mt5-bridge --vm mt5-win
```

See [docs/09](docs/09-windows-vm.md).

## Research on broker data (recommended)

Backtest the literal instruments the live strategy will trade, using the
broker's own history. Removes the roll-artefact and proxy-mismatch confounds
that have contaminated every commodity measurement — for free.

```
# in MT5, run ONCE each as a Script
mql5/DumpSymbols.mq5 · mql5/DumpHistory.mq5

python3 scripts/analyze_mt5.py --dir "<...>/MQL5/Files/tcs_history" --backtest --ftmo
```

See [docs/08](docs/08-mt5-research.md).

## Live execution (built, untested against a real broker)

```bash
python3 scripts/mock_ea.py --dir /tmp/tcs --watch &          # no broker needed
.venv/bin/python scripts/run_live.py --bridge-dir /tmp/tcs
.venv/bin/python scripts/run_live.py --summary               # reconciliation history
```

Guard → file bridge → MQL5 EA under Wine. The guard sizes the book to the
remaining loss headroom and flattens before the firm's engine can fire; the EA
re-checks both floors independently. See
[docs/07](docs/07-live-execution.md), including the Wine setup order.

## Track 2 — Order flow (research skill-building, not yet built)

Microstructure pipeline on crypto (free full-depth book + aggressor-tagged
trades), intended as a learning vehicle and a measurement harness rather than a
profit centre. Rationale, costs, and the Jigsaw/Databento/Rithmic/Tradovate
analysis are in [docs/01-stack-and-costs.md](docs/01-stack-and-costs.md).

## Non-negotiables

- `tests/test_causality.py` must pass. It proves the engine cannot see the
  future. Every performance number is void if it fails.
- Cumulatively count every parameter variant tested and compare Sharpe against
  the noise bar printed by the runner.
- Costs are charged on all turnover. Zero-cost backtests are meaningless.
- The guard and the EA must BOTH veto independently. Never remove one on the
  grounds that the other covers it.
- Reconciliation compares LOTS, not weights. Comparing weights hides rounding
  to the broker's volume step, which is the most common live-vs-sim bug.
- Prop-firm configurations must agree across all three intraday models
  (`close` / `brownian` / `worst`). A setting that only works under the
  optimistic model is not a setting, it is an artefact.
