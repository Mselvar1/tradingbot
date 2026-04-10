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
    from services.data.capital_epics import is_capital_supported
    if is_capital_supported(ticker):
        try:
            from services.data.capital import capital_client
            if not capital_client.session_token:
                await capital_client.create_session()
            epic = __import__(
                'services.data.capital_epics',
                fromlist=['get_epic']
            ).get_epic(ticker)
            price_data = await capital_client.get_price(epic)
            candles = await capital_client.get_candles(epic, "MINUTE_5", 100)
            if price_data["price"] > 0 and len(candles) > 10:
                closes = candles
                gains = [closes[i]-closes[i-1]
                         for i in range(1, len(closes))
                         if closes[i] > closes[i-1]]
                losses = [closes[i-1]-closes[i]
                          for i in range(1, len(closes))
                          if closes[i] < closes[i-1]]
                avg_gain = sum(gains[-14:])/14 if gains else 0
                avg_loss = sum(losses[-14:])/14 if losses else 0.001
                rs = avg_gain / avg_loss
                rsi = round(100 - (100 / (1 + rs)), 1)
                ma20 = round(sum(closes[-20:])/min(20, len(closes)), 5)
                ma50 = round(sum(closes[-50:])/min(50, len(closes)), 5)
                day_high = price_data.get("high", max(closes[-20:]))
                day_low = price_data.get("low", min(closes[-20:]))
                atr = round(day_high - day_low, 5)
                support = round(min(closes[-10:]), 5)
                resistance = round(max(closes[-10:]), 5)
                return {
                    "ticker": ticker,
                    "price": price_data["price"],
                    "prev_close": closes[0] if closes else price_data["price"],
                    "change_pct": price_data.get("change_pct", 0),
                    "rsi": rsi,
                    "ma20": ma20,
                    "ma50": ma50,
                    "day_high": day_high,
                    "day_low": day_low,
                    "atr": atr,
                    "volume_ratio": 1.0,
                    "support": support,
                    "resistance": resistance,
                    "candles_available": len(closes),
                    "source": "capital.com"
                }
        except Exception as e:
            print(f"Capital.com data failed for {ticker}: {e}")

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
        gains = [closes[i]-closes[i-1]
                 for i in range(1, len(closes)) if closes[i] > closes[i-1]]
        losses = [closes[i-1]-closes[i]
                  for i in range(1, len(closes)) if closes[i] < closes[i-1]]
        avg_gain = sum(gains[-14:])/14 if gains else 0
        avg_loss = sum(losses[-14:])/14 if losses else 0.001
        rs = avg_gain / avg_loss
        rsi = round(100 - (100 / (1 + rs)), 1)
        ma20 = round(sum(closes[-20:])/min(20, len(closes)), 2)
        ma50 = round(sum(closes[-50:])/min(50, len(closes)), 2)
        day_high = max(highs) if highs else current
        day_low = min(lows) if lows else current
        atr = round(day_high - day_low, 4)
        avg_vol = sum(volumes[-20:])/min(20, len(volumes)) if volumes else 0
        cur_vol = volumes[-1] if volumes else 0
        vol_ratio = round(cur_vol/avg_vol, 2) if avg_vol > 0 else 0
        change_pct = round(
            (current - prev_close) / prev_close * 100, 2
        ) if prev_close else 0
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
            "candles_available": len(closes),
            "source": "yahoo"
        }
    except Exception as e:
        price_data = await get_price(ticker)
        price_data.update({
            "rsi": 50, "ma20": price_data.get("price", 0),
            "ma50": price_data.get("price", 0),
            "atr": 0, "volume_ratio": 1,
            "change_pct": 0, "support": price_data.get("price", 0),
            "resistance": price_data.get("price", 0),
            "source": "yahoo_fallback"
        })
        return price_data