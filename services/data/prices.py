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

async def get_intraday(ticker: str) -> dict:
    url = (f"https://query1.finance.yahoo.com"
           f"/v8/finance/chart/{ticker}?interval=5m&range=1d")
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as s:
        r = await s.get(url, headers=headers)
        d = await r.json()
    try:
        result = d["chart"]["result"][0]
        meta = result["meta"]
        indicators = result["indicators"]["quote"][0]
        closes = [c for c in indicators.get("close", []) if c is not None]
        highs = [h for h in indicators.get("high", []) if h is not None]
        lows = [l for l in indicators.get("low", []) if l is not None]
        volumes = [v for v in indicators.get("volume", []) if v is not None]

        if len(closes) < 5:
            return {"ticker": ticker, "error": "insufficient data"}

        current = closes[-1]
        prev_close = meta.get("previousClose", closes[0])

        gains = [closes[i]-closes[i-1] for i in range(1,len(closes)) if closes[i]>closes[i-1]]
        losses = [closes[i-1]-closes[i] for i in range(1,len(closes)) if closes[i]<closes[i-1]]
        avg_gain = sum(gains[-14:])/14 if gains else 0
        avg_loss = sum(losses[-14:])/14 if losses else 0.001
        rs = avg_gain / avg_loss
        rsi = round(100 - (100 / (1 + rs)), 1)

        ma20 = round(sum(closes[-20:])/min(20,len(closes)), 2) if closes else 0
        ma50 = round(sum(closes[-50:])/min(50,len(closes)), 2) if closes else 0

        day_high = max(highs) if highs else current
        day_low = min(lows) if lows else current
        atr = round(day_high - day_low, 4)

        avg_vol = sum(volumes[-20:])/min(20,len(volumes)) if volumes else 0
        cur_vol = volumes[-1] if volumes else 0
        vol_ratio = round(cur_vol/avg_vol, 2) if avg_vol > 0 else 0

        change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0

        recent_closes = closes[-10:]
        support = round(min(recent_closes), 4)
        resistance = round(max(recent_closes), 4)

        return {
            "ticker": ticker,
            "price": current,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "rsi": rsi,
            "ma20": ma20,
            "ma50": ma50,
            "day_high": round(day_high, 4),
            "day_low": round(day_low, 4),
            "atr": atr,
            "volume_ratio": vol_ratio,
            "support": support,
            "resistance": resistance,
            "candles_available": len(closes)
        }
    except Exception as e:
        price_data = await get_price(ticker)
        price_data["rsi"] = 50
        price_data["ma20"] = price_data.get("price", 0)
        price_data["ma50"] = price_data.get("price", 0)
        price_data["atr"] = 0
        price_data["volume_ratio"] = 1
        price_data["change_pct"] = 0
        price_data["support"] = price_data.get("price", 0)
        price_data["resistance"] = price_data.get("price", 0)
        return price_data