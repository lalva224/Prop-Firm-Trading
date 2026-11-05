# Windows VM for MT5: an internal VPS

Built 2026-10-27. Runs **native Windows MT5** on the always-on Linux mini PC,
with no Wine, no OS reinstall and nothing deleted.

```
Mini PC — Linux host, always on, 16 cores / 18GB
├── research, strategy, repo            12 cores / ~12GB
│        ↕  SMB share on the libvirt NAT network
└── KVM VM: Windows + native MT5 + EA    4 cores / 6GB
```

## Why a VM and not Wine

MetaQuotes ships no native Linux build, so **every** Linux method — the official
`mt5ubuntu.sh`, every Docker image, Bottles, Lutris — is Wine underneath. Wine
*translates* Windows API calls; it is not Windows, and the instability, the
rendering bugs and the update breakage all come from the translating.

A KVM VM **runs real Windows**. MT5 is a native Windows app talking to a real
Windows kernel, on hardware-accelerated virtualisation. Every Wine failure mode
disappears, and the overhead is irrelevant for an app placing a handful of
orders a day.

| | Wine | KVM VM |
|---|---|---|
| What runs | API translation layer | **real Windows kernel** |
| MT5 stability | degraded | **native** |
| MT5 updates | can break the terminal | just work |
| Rollback | repair a Wine prefix | **`virsh snapshot-revert`** |
| Headless | Xvfb + VNC, hand-built | SPICE console, built in |

The non-Wine alternatives were only two: run MT5 on a Windows machine, or stop
using MT5. FTMO US offers only MT5 or TradingView, and TradingView cannot place
orders programmatically — so MT5 is forced, and this is how it runs on Linux
without Wine.

## Setup

```bash
bash scripts/vm/00-host-setup.sh                                    # KVM, libvirt, SMB share
bash scripts/vm/01-create-vm.sh --windows-iso ~/iso/Win11.iso       # or --win10
virt-viewer --connect qemu:///system mt5-win                        # run Windows setup
```

`00-host-setup.sh` verifies AMD-V/VT-x and `/dev/kvm`, installs qemu-kvm,
libvirt, OVMF (UEFI) and swtpm (TPM 2.0), starts the libvirt NAT network, and
creates `~/mt5-bridge` exported over SMB **restricted to 192.168.122.0/24** —
the VM network only, not reachable from the LAN.

`01-create-vm.sh` builds a q35 machine with UEFI + Secure Boot + emulated TPM
2.0 (Windows 11 requires all three; `--win10` drops them), a virtio disk, and
the virtio driver ISO attached as a second CD.

### The one non-obvious install step

**Windows setup will show no disks.** That is expected, not a failure: the
installer has no virtio driver. Click **Load driver**, browse the virtio CD, and
pick `amd64\<your windows version>\viostor`. The disk appears.

The disk is virtio rather than SATA because emulated SATA is markedly slower.
One extra click at install time is worth it.

### After Windows is installed

1. Run `virtio-win-guest-tools.exe` from the virtio CD — network and balloon drivers
2. Map the bridge: `net use Z: \\192.168.122.1\mt5bridge /persistent:yes`
3. Install MT5, log in to the FTMO account
4. **Tools → Options → Expert Advisors → enable algo trading** (orders are silently rejected otherwise)
5. **Disable MT5 auto-update** — an update prompt is a modal dialog, and a modal dialog blocks a headless terminal indefinitely
6. Copy `mql5/*.mq5` into `MQL5\Experts` and `MQL5\Scripts`, compile, attach `TcsBridge` to one chart
7. **Snapshot immediately:** `bash scripts/vm/02-snapshot.sh mt5-win working-baseline`

Keep it to **one chart**. Fewer charts means less memory and fewer rendering
paths to go wrong.

## Snapshots are the point

```bash
bash scripts/vm/02-snapshot.sh mt5-win before-mt5-update
bash scripts/vm/02-snapshot.sh mt5-win --list
bash scripts/vm/02-snapshot.sh mt5-win --revert working-baseline
```

Snapshots include memory state, so a revert returns to a **running terminal
with the EA already attached**, not a cold boot. Take one before every MT5
update, EA change, or config edit.

## Surviving a power cut unattended

Three settings turn an outage into a non-event:

| | |
|---|---|
| `virsh autostart mt5-win` | set by `01-create-vm.sh` — VM starts with the host |
| BIOS: **restore power state after AC loss** | the mini PC boots itself |
| A ~$60 UPS | covers short blips entirely |

Then: power returns → host boots → VM boots → MT5 launches → EA reattaches →
heartbeat resumes. No intervention.

This is the only place a self-hosted VM is genuinely worse than a rented VPS —
your power and your ISP have no redundancy. For a once-a-day rebalance on
positions held for weeks, a missed day is a small drift, not a lost trade.

## Preflight

```bash
python3 scripts/preflight.py --bridge-dir ~/mt5-bridge --vm mt5-win
```

Checks in dependency order, so the first failure is the cause rather than a
symptom:

```
config -> VM running -> share writable -> EA heartbeat -> broker connected
-> account matches .env -> guard headroom -> reconciliation history
```

Exit 0 means safe to trade. Three checks deserve note:

- **Heartbeat, not process liveness.** A wedged MT5 with a stuck modal dialog
  looks perfectly alive to `pgrep`. The EA writes a timestamp every 5s; if it
  stops, the terminal is alive-but-stuck — a state no process check can see.
- **Equity vs `FTMO_INITIAL_BALANCE`.** The guard derives *both* loss limits
  from the initial balance, so a wrong value makes it believe it has the wrong
  allowance. Preflight fails if equity is more than 2x off.
- **WRONG-SIGN divergences** in the last 10 reconciliation runs are a hard fail:
  live positions opposite the target means nothing else should proceed.

## Sizing notes for this host

18GB total, so 6GB to the guest leaves ~12GB for research — comfortable. 4 of
16 vCPUs to the guest leaves 12 for backtests. `--cpu host-passthrough` gives
the guest the real CPU feature set rather than a generic model.

Under 12GB of host RAM, drop the guest to 4GB (`--memory 4096`).
