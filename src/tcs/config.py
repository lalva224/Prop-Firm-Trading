"""Credential and parameter loading from .env.

No secrets live in code or in git. `.env` is gitignored; `.env.example` is the
committed template. Values are read once at import and validated on demand, so
a missing credential fails loudly at startup rather than mid-session with a
position open.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH, override: bool = False) -> dict[str, str]:
    """Minimal .env parser - no dependency, no surprises.

    Supports KEY=value, # comments, blank lines, and optional surrounding
    quotes. Does not support multiline values or variable interpolation.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if not key:
            continue
        values[key] = val
        if override or key not in os.environ:
            os.environ[key] = val
    return values


load_env()


def get(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(key) or None
    if required and not val:
        raise RuntimeError(
            f"{key} is not set. Copy .env.example to .env and fill it in."
        )
    return val if val is not None else default


def getf(key: str, default: float) -> float:
    raw = os.environ.get(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class AccountConfig:
    """Account parameters the risk guard needs.

    Both FTMO loss limits are fractions of the INITIAL balance, never of
    current equity, so the initial balance must be recorded here and never
    updated as the account grows.
    """
    initial_balance: float
    max_daily_loss: float
    max_total_loss: float
    target_vol: float
    rebalance_time: str
    rebalance_tz: str

    @property
    def daily_loss_cash(self) -> float:
        return self.initial_balance * self.max_daily_loss

    @property
    def floor_cash(self) -> float:
        return self.initial_balance * (1.0 - self.max_total_loss)

    @classmethod
    def from_env(cls) -> "AccountConfig":
        return cls(
            initial_balance=getf("FTMO_INITIAL_BALANCE", 200_000.0),
            max_daily_loss=getf("FTMO_MAX_DAILY_LOSS", 0.05),
            max_total_loss=getf("FTMO_MAX_TOTAL_LOSS", 0.10),
            target_vol=getf("FTMO_TARGET_VOL", 0.12),
            rebalance_time=get("REBALANCE_TIME", "16:30"),
            rebalance_tz=get("REBALANCE_TZ", "America/New_York"),
        )


def platform_status() -> dict[str, object]:
    """Which broker path is configured, and how this host can reach MT5.

    The official `MetaTrader5` package needs Windows Python plus a running MT5
    terminal. That does NOT mean a Linux host is excluded - it means the
    terminal lives elsewhere and this host talks to an agent. Three valid
    topologies:

      native  - this host is Windows, MetaTrader5 imports directly
      remote  - MT5 runs on another machine; we call an agent over the network
                (a Tailscale tailnet is ideal: authenticated, encrypted, no
                port forwarding, works through NAT)
      wine    - MT5 under Wine on this host, with Windows Python in the same
                prefix. One place, always on, but the fiddliest to set up.
    """
    import sys

    ctrader = all(get(k) for k in ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET",
                                   "CTRADER_ACCESS_TOKEN", "CTRADER_ACCOUNT_ID"))
    mt5 = all(get(k) for k in ("FTMO_MT5_LOGIN", "FTMO_MT5_PASSWORD",
                               "FTMO_MT5_SERVER"))
    native = sys.platform.startswith("win")
    agent = get("MT5_AGENT_URL")

    if native:
        topology = "native"
    elif agent:
        topology = "remote"
    elif get("MT5_WINE_PREFIX"):
        topology = "wine"
    else:
        topology = "unconfigured"

    return {
        "ctrader_configured": ctrader,
        "mt5_configured": mt5,
        "mt5_topology": topology,
        "mt5_agent_url": agent,
        "mt5_reachable": topology != "unconfigured",
        "os": sys.platform,
        "databento_configured": bool(get("DATABENTO_API_KEY")),
    }
