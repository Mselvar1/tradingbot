import aiohttp
from config.settings import settings

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"

async def get_geopolitical_news() -> list:
    queries = [
        "war conflict sanctions geopolitical",
        "Federal Reserve interest rates inflation",
        "oil supply OPEC energy crisis",
        "China US trade tariffs",
        "banking crisis financial stability"
    ]
    all_articles = []
    async with aiohttp.ClientSession() as s:
        for q in queries:
            try:
                params = {
                    "q": q,
                    "sortBy": "publishedAt",
                    "pageSize": 3,
                    "language": "en",
                    "apiKey": settings.newsapi_key
                }
                r = await s.get(NEWSAPI_URL, params=params)
                d = await r.json()
                for a in d.get("articles", []):
                    if a.get("title") and "[Removed]" not in a["title"]:
                        all_articles.append({
                            "title": a["title"],
                            "source": a["source"]["name"],
                            "published": a["publishedAt"][:10],
                            "category": q.split()[0]
                        })
            except Exception:
                continue
    return all_articles[:15]

async def get_market_sentiment() -> dict:
    try:
        async with aiohttp.ClientSession() as s:
            vix_url = (
                "https://query1.finance.yahoo.com"
                "/v8/finance/chart/%5EVIX?interval=1d&range=2d"
            )
            headers = {"User-Agent": "Mozilla/5.0"}
            r = await s.get(vix_url, headers=headers)
            d = await r.json()
            meta = d["chart"]["result"][0]["meta"]
            vix = meta.get("regularMarketPrice", 0)
            if vix > 30:
                regime = "high fear - risk off"
            elif vix > 20:
                regime = "elevated volatility - caution"
            elif vix > 15:
                regime = "normal - neutral"
            else:
                regime = "low volatility - risk on"
            return {
                "vix": vix,
                "regime": regime,
                "risk_off": vix > 25
            }
    except Exception:
        return {"vix": 0, "regime": "unknown", "risk_off": False}

async def get_sector_news(ticker: str) -> list:
    sector_map = {
        "NVDA": "semiconductor AI chips",
        "AAPL": "Apple iPhone technology",
        "TSLA": "Tesla electric vehicles",
        "MSFT": "Microsoft cloud AI",
        "ASML": "semiconductor lithography",
        "SHEL": "oil energy Shell",
        "GC=F": "gold precious metals",
        "SI=F": "silver precious metals",
        "CL=F": "crude oil OPEC",
        "BTC-USD": "Bitcoin cryptocurrency",
        "ETH-USD": "Ethereum cryptocurrency",
        "EURUSD=X": "Euro dollar ECB Fed",
        "^GSPC": "S&P 500 stock market"
    }
    query = sector_map.get(ticker, ticker)
    try:
        async with aiohttp.ClientSession() as s:
            params = {
                "q": query,
                "sortBy": "publishedAt",
                "pageSize": 5,
                "language": "en",
                "apiKey": settings.newsapi_key
            }
            r = await s.get(NEWSAPI_URL, params=params)
            d = await r.json()
            return [
                {
                    "title": a["title"],
                    "source": a["source"]["name"],
                    "published": a["publishedAt"][:10]
                }
                for a in d.get("articles", [])
                if a.get("title") and "[Removed]" not in a["title"]
            ]
    except Exception:
        return []