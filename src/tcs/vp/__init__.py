"""Volume Profile / Market Profile strategies.

Auction Market Theory: the market alternates between BALANCE (rotational, price
oscillating around an accepted value) and IMBALANCE (directional, trend day).
In balance, the extremes of the Value Area attract responsive orders and the
POC acts as a magnet. In imbalance, fading the extremes gets you run over.

This package implements the balance trade: responsive short at VAH, responsive
long at VAL, target POC, with an explicit balance gate to stay out of trend days.
"""

from .profile import VolumeProfile, build_profile, profile_from_trades
from .backtest import VPConfig, VPResult, Trade, run_vp

__all__ = [
    "VolumeProfile",
    "build_profile",
    "profile_from_trades",
    "VPConfig",
    "VPResult",
    "Trade",
    "run_vp",
]
