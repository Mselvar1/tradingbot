import aiohttp

async def get_price(ticker: str) -> dict:
    url = (f"https://query1.finance.yahoo.com"
           f"/v8/finance/chart/{ticker}?interval=1d&range=5d")
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as s:
        r = await s.get(url, headers=headers)
        d = await r.json()
    try:
        meta = d["chart"]["result"][0]["meta"]
        return {
            "ticker": ticker,
            "price": meta.get("regularMarketPrice", 0),
            "prev_close": meta.get("previousClose", 0),
            "currency": meta.get("currency", "USD")
        }
    except Exception as e:
        return {"ticker": ticker, "price": 0, "error": str(e)}