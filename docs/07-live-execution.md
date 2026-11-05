# Live Execution: guard, bridge, EA

Built 2026-10-27. Three components, each independently testable, and a mock EA
so the whole Linux side runs today with no MT5, no Wine and no broker.

```bash
# 1. everything on the Linux side, no broker at all
python3 scripts/mock_ea.py --dir /tmp/tcs --watch &
.venv/bin/python scripts/run_live.py --bridge-dir /tmp/tcs --login 9999999

# 2. compute and print, write nothing
.venv/bin/python scripts/run_live.py --bridge-dir /tmp/tcs --dry-run

# 3. reconciliation history
.venv/bin/python scripts/run_live.py --summary

python3 -m pytest tests/ -q          # 57 tests
```

## Architecture

```
┌─ Linux mini PC (always on) ──────────────┐
│  strategy      ewmac_blend -> weights     │
│  guard         src/tcs/guard.py           │  ← sizes the book to the
│  bridge        src/tcs/bridge.py          │     remaining loss headroom
│  reconcile     src/tcs/reconcile.py       │
│  orchestrator  scripts/run_live.py        │
└───────────────┬───────────────────────────┘
                │  JSON/CSV files in MQL5\Files
┌───────────────▼───────────────────────────┐
│  MT5 under Wine                           │
│  mql5/TcsBridge.mq5                       │  ← its own independent guard
└───────────────────────────────────────────┘
```

## Why files rather than a socket

MT5's `MQL5\Files` directory is an ordinary Linux folder under Wine, so both
sides share it with no ports, no auth and no firewall. For a once-a-day
rebalance, latency is irrelevant, and every instruction and response is left on
disk for inspection afterwards. A socket would add failure modes and buy
nothing.

| file | direction | contents |
|---|---|---|
| `tcs_targets.csv` | Linux → EA | weights + guard decision. **CSV, not JSON** — MQL5 has no JSON parser and a hand-rolled one would be the least reliable code in the system |
| `tcs_targets.json` | Linux → (audit) | same payload, human-readable. The EA never reads it |
| `tcs_state.json` | EA → Linux | equity, balance, day-open balance, positions, fills, errors |
| `tcs_symbols.json` | EA → Linux | broker symbol specs, so this side can reproduce the EA's sizing exactly |
| `tcs_heartbeat.json` | EA → Linux | proof the terminal is alive |
| `tcs_dayopen.txt` | EA → itself | day-open balance, so the daily floor survives a terminal restart |

**Safety properties:** atomic writes (`.tmp` then rename, so no reader ever
sees a half-written file) · a **nonce** per instruction, echoed by the EA, so a
file left on disk from yesterday can never be replayed · **staleness checks on
both sides**, so a wedged terminal causes inaction rather than stale action ·
**fail flat, never fail frozen** — every error path resolves to no position.

## The guard

The firm's limit is *detection*, not prevention: by the time FTMO's 5% daily
limit fires, the account has already failed. Prevention has to happen before
the order is sent, so the limits are re-implemented here.

**Both floors are fractions of the INITIAL balance and never grow:**

```
total floor  = initial * (1 - max_total_loss)           fixed forever
daily floor  = day_open_balance - initial * max_daily    resets each day
binding floor = max(total, daily)       usually the daily one
headroom      = EQUITY (incl. floating P&L) - binding floor
```

It **sizes rather than vetoes.** A veto-only guard blocks the order after the
book is already too big. This one estimates the book's adverse one-day move
from the full covariance matrix at `tail_sigmas` (default 3σ), and scales the
target until that fits inside `safety_margin` (default 50%) of headroom.

Note the deliberate difference from `ftmo.py`: `brownian_adverse` uses 0.8σ,
the *expected* within-day minimum, which is right for estimating average damage
and wrong for a risk limit. The guard must survive a bad day, not a typical one.

### Measured behaviour across an intraday loss

| day-open | equity | verdict | scale | headroom |
|---|---|---|---|---|
| 200,000 | 200,000 | APPROVED | 1.000 | 10,000 |
| 200,000 | 196,000 | SCALED | 0.833 | 6,000 |
| 200,000 | 192,000 | SCALED | **0.283** | 2,000 |
| 200,000 | 190,400 | **FLATTEN** | 0.000 | 400 |
| 200,000 | 189,500 | **FLATTEN** | 0.000 | −500 |

Monotonic de-risking, then a flatten with the allowance 96% consumed — before
the firm's engine has anything to fire at. This is the behaviour a human
reliably fails to execute under stress, which is the entire argument for
automating it.

## The EA

`mql5/TcsBridge.mq5`, 522 lines. It recomputes both floors itself and vetoes
independently of the Linux side — two checks, either of which can stop a trade,
because the EA is the process actually holding positions.

**Sizing:** `notional_per_lot = bid * (tick_value / tick_size)`. `tick_value`
is already denominated in the account currency, so this is correct for FX,
metals and index CFDs with no per-instrument special-casing. Volumes are
**floored** to the broker's step (never oversize) and zeroed below the minimum.

**Sign flips are executed as close-then-open**, so results are identical on
netting and hedging accounts.

**Day boundary:** the day-open balance is persisted to disk, so the daily floor
is correct after a terminal restart rather than silently resetting to current
balance.

## The mock EA

`scripts/mock_ea.py` reads `tcs_targets.csv` exactly as the real EA does,
applies the same sizing and rounding, and writes state back. Nothing connects
to a broker.

Wine, MT5 and a demo account are three independent things that can each take an
afternoon. The mock lets the guard, bridge, orchestrator and reconciliation log
all be validated first, so that when the real EA is attached **the only new
variable is MT5 itself**.

Failure injection: `--fail-symbol SYM` (rejection) · `--unknown-symbol SYM`
(not listed by the broker) · `--reject-all` · `--day-start N` (simulate an
intraday loss, which is how the flatten path gets tested).

## Reconciliation — the actual deliverable of demo testing

Passing a demo challenge proves nothing. Proving the live book matches the
simulated book proves the bot works. Comparison is in **lots**, not weights,
because rounding to the broker's volume step is the bug class that matters
most — and comparing weights hides it entirely.

Verified detection, with bugs deliberately injected:

```
QQQ    0.70   0.00  -0.70   missing live - order never placed or was rejected
TLT   -1.25   0.00   1.25   missing live - order never placed or was rejected
note: VNQ: no broker spec published yet
note: EA error: unknown symbol VNQ - check the broker's naming
```

Reasons are enumerated rather than generic: `missing live` · `unexpected live`
· `rounded to zero - below the broker's minimum volume` · **`WRONG SIGN`** ·
`size mismatch`. A wrong-sign event is called out loudly in `--summary`,
because it means the position is the opposite of intended and nothing else
should proceed until it is fixed.

Appended as JSONL to `out/reconcile.jsonl` — greppable, never rewritten.

## Wine setup on the mini PC, in order

1. `WINEARCH=win64 WINEPREFIX=~/.mt5 winecfg`
2. `winetricks vcrun2019 corefonts` — without these the terminal renders blank
3. Install the broker's `mt5setup.exe` into that prefix
4. **Run under Xvfb.** The mini PC is headless and MT5 needs a display. This is
   the fiddliest step and the one that catches people out.
5. Enable **Algo Trading** (Tools → Options → Expert Advisors), or orders are
   silently rejected
6. Disable MT5 auto-update, and **pin the Wine version** — an upgrade that
   breaks the terminal causes a missed rebalance
7. Copy `mql5/TcsBridge.mq5` into `MQL5/Experts`, compile, attach to any chart
8. Set `InpInitialBalance` to the **exact** issued account size — every limit
   derives from it
9. systemd units: Xvfb → MT5 → watchdog, with restart-on-failure
10. Point `--bridge-dir` at
    `~/.mt5/drive_c/users/<user>/AppData/Roaming/MetaQuotes/Terminal/<hash>/MQL5/Files`

**Speed is not the concern.** Wine translates API calls rather than emulating
instructions, and it does not touch the network stack, so order latency is
unchanged. Its real overhead is GDI/graphics, which a headless setup never
uses. The genuine Wine risks are all reliability: a wedged terminal, a Wine
upgrade regression, an MT5 auto-update, Xvfb dying. Each causes a **missed
rebalance**, which is what can actually fail a funded account — hence the
heartbeat and the watchdog are not optional.

## Not built yet

- **Economic-calendar blackout.** FTMO forbids opening or closing within 2
  minutes of a high-impact release. A daily rebalance at a fixed time clear of
  the calendar avoids it structurally, but the router should still refuse to
  submit inside a window.
- **systemd units and the watchdog** (steps 9 above).
- Kill switch on N consecutive divergent reconciliations.
