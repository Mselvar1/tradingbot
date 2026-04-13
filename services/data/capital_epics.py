EPIC_MAP = {
    "BTC-USD": "BTCUSD",
    "ETH-USD": "ETHEREUM",
    "NVDA": "NVDA",
    "XAUUSD=X": "GOLD",
    "GC=F": "GOLD",
    "GLD": "GOLD",
    "XAGUSD=X": "SILVER",
    "CL=F": "OIL_CRUDE",
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "AUDUSD=X": "AUDUSD",
    "USDCHF=X": "USDCHF",
    "^GSPC": "US500",
    "^NDX": "USTEC",
    "^DJI": "US30",
    "^FTSE": "UK100",
    "^GDAXI": "GERMANY40",
    "AAPL": "AAPL",
    "TSLA": "TSLA",
    "MSFT": "MSFT",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "META": "META",
    "AMD": "AMD",
    "ASML": "ASML",
}

def get_epic(ticker: str) -> str:
    return EPIC_MAP.get(ticker.upper(), ticker)

def is_capital_supported(ticker: str) -> bool:
    return ticker.upper() in EPIC_MAP