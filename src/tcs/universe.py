"""Instrument universes.

Two universes are provided deliberately:

ETF_UNIVERSE   - clean, free, long-history, no roll artifacts. Use this to
                 validate that the trend premium exists in your code at all.
FUTURES_PROXY  - yfinance continuous front-month futures. Closer to the real
                 implementation vehicle, BUT these series contain roll gaps and
                 are not properly back-adjusted. Treat results as indicative
                 only; replace with Databento / CSI / Norgate back-adjusted
                 series before trusting any number from this universe.
"""

from __future__ import annotations

# ticker -> (description, asset class)
ETF_UNIVERSE: dict[str, tuple[str, str]] = {
    # Equity
    "SPY": ("S&P 500", "equity"),
    "QQQ": ("Nasdaq 100", "equity"),
    "IWM": ("Russell 2000", "equity"),
    "EFA": ("Developed ex-US", "equity"),
    "EEM": ("Emerging markets", "equity"),
    "EWJ": ("Japan", "equity"),
    # Rates
    "TLT": ("US 20y+ Treasury", "bond"),
    "IEF": ("US 7-10y Treasury", "bond"),
    "SHY": ("US 1-3y Treasury", "bond"),
    "LQD": ("IG credit", "bond"),
    "HYG": ("HY credit", "bond"),
    # FX
    "UUP": ("USD index", "fx"),
    "FXE": ("EUR", "fx"),
    "FXY": ("JPY", "fx"),
    # Commodity
    "GLD": ("Gold", "commodity"),
    "SLV": ("Silver", "commodity"),
    "USO": ("WTI crude", "commodity"),
    "DBA": ("Agriculture", "commodity"),
    "DBB": ("Base metals", "commodity"),
    # Real assets
    "VNQ": ("US REITs", "equity"),
}

FUTURES_PROXY: dict[str, tuple[str, str]] = {
    "ES=F": ("S&P 500", "equity"),
    "NQ=F": ("Nasdaq 100", "equity"),
    "YM=F": ("Dow", "equity"),
    "RTY=F": ("Russell 2000", "equity"),
    "ZN=F": ("10y Note", "bond"),
    "ZB=F": ("30y Bond", "bond"),
    "ZF=F": ("5y Note", "bond"),
    "6E=F": ("EUR/USD", "fx"),
    "6J=F": ("JPY/USD", "fx"),
    "6B=F": ("GBP/USD", "fx"),
    "6A=F": ("AUD/USD", "fx"),
    "GC=F": ("Gold", "commodity"),
    "SI=F": ("Silver", "commodity"),
    "HG=F": ("Copper", "commodity"),
    "CL=F": ("WTI crude", "commodity"),
    "NG=F": ("Natural gas", "commodity"),
    "ZC=F": ("Corn", "commodity"),
    "ZS=F": ("Soybeans", "commodity"),
    "ZW=F": ("Wheat", "commodity"),
    "KC=F": ("Coffee", "commodity"),
}

CRYPTO_PERPS: dict[str, tuple[str, str]] = {
    "BTCUSDT": ("Bitcoin perp", "crypto"),
    "ETHUSDT": ("Ethereum perp", "crypto"),
    "SOLUSDT": ("Solana perp", "crypto"),
    "BNBUSDT": ("BNB perp", "crypto"),
    "XRPUSDT": ("XRP perp", "crypto"),
}

# What a prop firm actually lets you trade. No ETFs exist on any prop platform,
# so the ETF universe is a research substrate only - this is the real one.
#
# Proxies: cash indices stand in for index CFDs (CFDs track the cash index),
# spot FX for FX pairs, front futures for metals and energy. The mapping is
# close enough for a Sharpe estimate; spreads are wider than ETFs, so costs
# must be raised accordingly.
PROP_TRADEABLE: dict[str, tuple[str, str]] = {
    # FX majors - available at every prop firm, and the only class FundingPips
    # allows US accounts to trade.
    "EURUSD=X": ("EUR/USD", "fx"),
    "GBPUSD=X": ("GBP/USD", "fx"),
    "USDJPY=X": ("USD/JPY", "fx"),
    "AUDUSD=X": ("AUD/USD", "fx"),
    "USDCAD=X": ("USD/CAD", "fx"),
    "USDCHF=X": ("USD/CHF", "fx"),
    "NZDUSD=X": ("NZD/USD", "fx"),
    # Index CFDs
    "^GSPC": ("S&P 500", "equity"),
    "^NDX": ("Nasdaq 100", "equity"),
    "^DJI": ("Dow 30", "equity"),
    "^RUT": ("Russell 2000", "equity"),
    "^GDAXI": ("DAX 40", "equity"),
    "^FTSE": ("FTSE 100", "equity"),
    "^N225": ("Nikkei 225", "equity"),
    # Metals
    "GC=F": ("Gold", "commodity"),
    "SI=F": ("Silver", "commodity"),
    "HG=F": ("Copper", "commodity"),
    # Energy
    "CL=F": ("WTI crude", "commodity"),
    "NG=F": ("Natural gas", "commodity"),
    # Crypto CFDs - short history, included where the firm offers them
    "BTC-USD": ("Bitcoin", "crypto"),
    "ETH-USD": ("Ethereum", "crypto"),
}

# Bond exposure is the open question: our ETF universe held 5 bond instruments
# (TLT/IEF/SHY/LQD/HYG) and they were the key diversifier against equity beta.
# Most prop firms do NOT offer bond instruments. Measure both ways.
PROP_PLUS_BONDS: dict[str, tuple[str, str]] = {
    **PROP_TRADEABLE,
    "ZN=F": ("10y Note", "bond"),
    "ZB=F": ("30y Bond", "bond"),
    "ZF=F": ("5y Note", "bond"),
}

# What FTMO actually offers (166 instruments: 43 FX, 14 indices, 11 energy+softs,
# 9 metals, 30 crypto, 59 equities). NOTABLY ABSENT: any bond or rate
# instrument - and the bond/credit sleeve was the best in the whole system at
# Sharpe 0.87. This universe is chosen STRUCTURALLY: one liquid instrument per
# distinct market, decided before looking at any backtest (see docs/05).
#
# The compensation for losing bonds is breadth in commodities: 7 individual
# agricultural markets and 5 separate metals, where the ETF universe had only
# the DBA and DBB baskets.
FTMO_UNIVERSE: dict[str, tuple[str, str]] = {
    # Equity indices - 6 distinct regions (FTMO: US500 US100 US2000 GER40 JP225 UK100 HK50)
    "^GSPC": ("S&P 500 / US500", "equity"),
    "^NDX": ("Nasdaq 100 / US100", "equity"),
    "^RUT": ("Russell 2000 / US2000", "equity"),
    "^GDAXI": ("DAX / GER40", "equity"),
    "^N225": ("Nikkei / JP225", "equity"),
    "^FTSE": ("FTSE / UK100", "equity"),
    # FX majors - all dollar-driven, so 6 is already most of the factor
    "EURUSD=X": ("EUR/USD", "fx"),
    "GBPUSD=X": ("GBP/USD", "fx"),
    "USDJPY=X": ("USD/JPY", "fx"),
    "AUDUSD=X": ("AUD/USD", "fx"),
    "USDCAD=X": ("USD/CAD", "fx"),
    "USDCHF=X": ("USD/CHF", "fx"),
    # Metals - 5 genuinely separate markets (XAUUSD XAGUSD XPTUSD XPDUSD XCUUSD)
    "GC=F": ("Gold / XAUUSD", "metal"),
    "SI=F": ("Silver / XAGUSD", "metal"),
    "PL=F": ("Platinum / XPTUSD", "metal"),
    "PA=F": ("Palladium / XPDUSD", "metal"),
    "HG=F": ("Copper / XCUUSD", "metal"),
    # Energy (USOIL UKOIL NATGAS HEATOIL)
    "CL=F": ("WTI / USOIL", "energy"),
    "BZ=F": ("Brent / UKOIL", "energy"),
    "NG=F": ("Nat gas / NATGAS", "energy"),
    "HO=F": ("Heating oil / HEATOIL", "energy"),
    # Softs and grains - the real diversification gain over the ETF universe
    "CC=F": ("Cocoa / COCOA", "ag"),
    "KC=F": ("Coffee / COFFEE", "ag"),
    "ZC=F": ("Corn / CORN", "ag"),
    "ZS=F": ("Soybean / SOYBEAN", "ag"),
    "ZW=F": ("Wheat / WHEAT", "ag"),
    "CT=F": ("Cotton / COTTON", "ag"),
    "SB=F": ("Sugar / SUGAR", "ag"),
    # Crypto
    "BTC-USD": ("Bitcoin", "crypto"),
    "ETH-USD": ("Ethereum", "crypto"),
}

UNIVERSES = {
    "etf": ETF_UNIVERSE,
    "ftmo": FTMO_UNIVERSE,
    "futures": FUTURES_PROXY,
    "crypto": CRYPTO_PERPS,
    "prop": PROP_TRADEABLE,
    "prop_bonds": PROP_PLUS_BONDS,
}


def asset_classes(universe: dict[str, tuple[str, str]]) -> dict[str, str]:
    return {k: v[1] for k, v in universe.items()}
