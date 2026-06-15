"""
Expanded watchlist covering 200+ Indian stocks across all market caps.

Large-cap: stable, liquid, well-researched.
Mid-cap: more volatility, more opportunity, less analyst coverage.
Small-cap: highest volatility, highest potential, requires tighter stops.

The system adjusts risk parameters automatically by market-cap tier
(see get_risk_params_for_tier): smaller stocks get tighter stops and
smaller positions. A symbol listed in more than one tier resolves to the
LAST tier applied (mid/small win over large) — intentional, the smaller-cap
risk profile is the safer default.
"""
from __future__ import annotations

# Nifty 50 large-caps (tier 1 — most liquid)
LARGE_CAP = {
    "RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Banking",
    "ICICIBANK": "Banking", "INFY": "IT", "HINDUNILVR": "FMCG",
    "ITC": "FMCG", "SBIN": "Banking", "BHARTIARTL": "Telecom",
    "BAJFINANCE": "Finance", "WIPRO": "IT", "HCLTECH": "IT",
    "AXISBANK": "Banking", "KOTAKBANK": "Banking", "SUNPHARMA": "Pharma",
    "TITAN": "Consumer", "MARUTI": "Auto", "LT": "Infra",
    "NTPC": "Energy", "POWERGRID": "Energy", "COALINDIA": "Energy",
    "ONGC": "Energy", "JSWSTEEL": "Metals", "TATASTEEL": "Metals",
    "ADANIPORTS": "Infra", "ULTRACEMCO": "Cement", "GRASIM": "Cement",
    "DIVISLAB": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "BAJAJFINSV": "Finance", "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto",
    "TATACONSUM": "FMCG", "BRITANNIA": "FMCG", "NESTLEIND": "FMCG",
    "INDUSINDBK": "Banking", "BPCL": "Energy", "HINDALCO": "Metals",
    "APOLLOHOSP": "Healthcare", "EICHERMOT": "Auto", "TATAMOTORS": "Auto",
}

# Nifty Midcap 100 — higher opportunity, more volatility
MID_CAP = {
    "ABCAPITAL": "Finance", "APLAPOLLO": "Metals", "ATUL": "Chemicals",
    "AUBANK": "Banking", "BANDHANBNK": "Banking", "BATAINDIA": "Consumer",
    "BERGEPAINT": "Consumer", "BIOCON": "Pharma", "CHOLAFIN": "Finance",
    "COFORGE": "IT", "CONCOR": "Infra", "CROMPTON": "Consumer",
    "CUMMINSIND": "Engineering", "DALBHARAT": "Cement", "DEEPAKNTR": "Chemicals",
    "EMAMILTD": "FMCG", "ESCORTS": "Auto", "EXIDEIND": "Auto",
    "FEDERALBNK": "Banking", "GLAND": "Pharma", "GNFC": "Chemicals",
    "GODREJCP": "FMCG", "GODREJPROP": "Real Estate", "GRANULES": "Pharma",
    "GSPL": "Energy", "HFCL": "Telecom", "IDFCFIRSTB": "Banking",
    "IEX": "Energy", "IIFL": "Finance", "INDIAMART": "Technology",
    "INDIGO": "Aviation", "JKCEMENT": "Cement", "JUBLFOOD": "Consumer",
    "KANSAINER": "Consumer", "LICHSGFIN": "Finance", "LICI": "Finance",
    "LTTS": "IT", "LUPIN": "Pharma", "MARICO": "FMCG",
    "METROPOLIS": "Healthcare", "MFSL": "Finance", "MOTHERSON": "Auto",
    "MPHASIS": "IT", "MRF": "Auto", "NAVINFLUOR": "Chemicals",
    "NMDC": "Metals", "OFSS": "IT", "PAGEIND": "Consumer",
    "PERSISTENT": "IT", "PETRONET": "Energy", "PFIZER": "Pharma",
    "PHOENIX": "Real Estate", "PIDILITIND": "Chemicals", "PIIND": "Chemicals",
    "PNB": "Banking", "POLYCAB": "Engineering", "POONAWALLA": "Finance",
    "PRESTIGE": "Real Estate", "RAMCOCEM": "Cement", "RECLTD": "Finance",
    "SAIL": "Metals", "SBICARD": "Finance", "SBILIFE": "Finance",
    "SHREECEM": "Cement", "SIEMENS": "Engineering",
    "SRF": "Chemicals", "STARHEALTH": "Healthcare", "SUPREMEIND": "Consumer",
    "TATACOMM": "Telecom", "TATAELXSI": "IT", "TATAINVEST": "Finance",
    "TATAPOWER": "Energy", "TIINDIA": "Auto", "TORNTPHARM": "Pharma",
    "TORNTPOWER": "Energy", "TRENT": "Consumer", "TTKPRESTIG": "Consumer",
    "TVSMOTOR": "Auto", "UBL": "FMCG",
    "UNIONBANK": "Banking", "UNOMINDA": "Auto", "UPL": "Chemicals",
    "VEDL": "Metals", "VBL": "FMCG", "VOLTAS": "Engineering",
    "WHIRLPOOL": "Consumer", "ZOMATO": "Technology",
    "NYKAA": "Consumer",
}

# Liquid small-cap opportunities (higher risk, higher reward).
SMALL_CAP = {
    "AARTIIND": "Chemicals", "ABSLAMC": "Finance", "ACCELYA": "IT",
    "AIAENG": "Engineering", "AJANTPHARM": "Pharma", "ALKEM": "Pharma",
    "AMBER": "Consumer", "ANURAS": "Healthcare", "APARINDS": "Engineering",
    "ASTERDM": "Healthcare", "ASTRAL": "Consumer", "ATGL": "Energy",
    "BALAMINES": "Chemicals", "BALRAMCHIN": "FMCG", "BSOFT": "IT",
    "CANFINHOME": "Finance", "CAPLIPOINT": "Pharma", "CARERATING": "Finance",
    "CERA": "Consumer", "CLEAN": "Energy", "CRAFTSMAN": "Engineering",
    "DATAMATICS": "IT", "DCMSHRIRAM": "Chemicals", "DHANUKA": "Chemicals",
    "EIDPARRY": "FMCG", "ELECON": "Engineering", "ELGIRUBBER": "Auto",
    "EPIGRAL": "Chemicals", "FINEORG": "Chemicals", "FORCEMOT": "Auto",
    "GABRIEL": "Auto", "GESHIP": "Infra", "GHCL": "Chemicals",
    "GMMPFAUDLR": "Engineering", "GPPL": "Infra", "GRINDWELL": "Engineering",
    "GUJGASLTD": "Energy", "HAPPSTMNDS": "IT", "HLEGLAS": "Consumer",
    "INGERRAND": "Engineering", "INTELLECT": "IT", "IOB": "Banking",
    "IOLCP": "Chemicals", "IRCON": "Infra", "ITDCEM": "Infra",
    "JBCHEPHARM": "Pharma", "JKPAPER": "Consumer", "JLHL": "Healthcare",
    "JSWENERGY": "Energy", "KALYANKJIL": "Consumer", "KFINTECH": "Finance",
    "KIRLOSENG": "Engineering", "KRBL": "FMCG", "KSCL": "Chemicals",
    "LATENTVIEW": "IT", "LEMONTREE": "Consumer", "LINDEINDIA": "Chemicals",
    "LUXIND": "Consumer", "MANAPPURAM": "Finance", "MAZDOCK": "Engineering",
    "MEDPLUS": "Healthcare", "MIDHANI": "Engineering", "MOLD-TEK": "Consumer",
    "MTAR": "Engineering", "NATCOPHARM": "Pharma", "NBCC": "Infra",
    "NCLIND": "Chemicals", "NOCIL": "Chemicals", "NUVAMA": "Finance",
    "OLECTRA": "Auto", "ORIENTELEC": "Consumer", "PARADEEP": "Chemicals",
    "PARAS": "Healthcare", "PCJEWELLER": "Consumer", "PENIND": "Metals",
    "PNBHOUSING": "Finance", "PRINCEPIPE": "Consumer", "RADIOCITY": "Media",
    "RAJRATAN": "Auto", "RKFORGE": "Auto", "ROLEXRINGS": "Engineering",
    "ROUTE": "Technology", "SAFARI": "Consumer", "SAPPHIRE": "Consumer",
    "SHYAMMETL": "Metals", "SIGNATURE": "Real Estate", "SKIPPER": "Infra",
    "SMSPHARMA": "Pharma", "SOBHA": "Real Estate", "SOLARA": "Pharma",
    "SPANDANA": "Finance", "SPIC": "Chemicals", "SUNTECK": "Real Estate",
    "SUVENPHAR": "Pharma", "TANLA": "Technology", "TATACHEM": "Chemicals",
    "TEXRAIL": "Auto", "THYROCARE": "Healthcare", "TIMKEN": "Engineering",
    "TRIVENI": "Engineering", "UFLEX": "Consumer", "UJJIVANSFB": "Banking",
    "USHAMART": "Engineering", "UTIAMC": "Finance", "VAIBHAVGBL": "Consumer",
    "VARROC": "Auto", "VGUARD": "Consumer", "VIJAYA": "Finance",
    "VINATIORGA": "Chemicals", "VISHNU": "Chemicals", "VSTIND": "FMCG",
    "WELSPUNIND": "Consumer", "WESTLIFE": "Consumer", "ZEEL": "Media",
}

# Combined watchlist with tier information.
ALL_STOCKS: dict[str, dict[str, str]] = {}
for _sym, _sec in LARGE_CAP.items():
    ALL_STOCKS[_sym] = {"sector": _sec, "tier": "large"}
for _sym, _sec in MID_CAP.items():
    ALL_STOCKS[_sym] = {"sector": _sec, "tier": "mid"}
for _sym, _sec in SMALL_CAP.items():
    ALL_STOCKS[_sym] = {"sector": _sec, "tier": "small"}


def get_risk_params_for_tier(tier: str) -> dict:
    """Risk parameters per market-cap tier.

    Smaller stocks = tighter stops, smaller positions, stricter volume confirmation.
    """
    if tier == "large":
        return {
            "stop_loss_multiplier": 1.0,     # standard stop
            "position_size_multiplier": 1.0,  # full Kelly size
            "min_volume_ratio": 1.0,          # standard volume check
            "atr_multiplier": 2.0,            # standard ATR stop
        }
    if tier == "mid":
        return {
            "stop_loss_multiplier": 1.2,     # slightly tighter
            "position_size_multiplier": 0.8,  # 80% of Kelly
            "min_volume_ratio": 1.2,          # need more volume confirmation
            "atr_multiplier": 1.8,
        }
    # small (and any unknown tier — default to the most conservative profile)
    return {
        "stop_loss_multiplier": 1.5,     # much tighter stop
        "position_size_multiplier": 0.5,  # half Kelly (more volatile)
        "min_volume_ratio": 1.5,          # need strong volume
        "atr_multiplier": 1.5,            # tighter ATR stop
    }


def get_tier(symbol: str) -> str:
    """Tier for a symbol, defaulting to 'large' for unknown names."""
    return ALL_STOCKS.get(symbol, {}).get("tier", "large")
