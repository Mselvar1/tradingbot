import aiohttp

async def get_fear_greed() -> dict:
    try:
        url = "https://api.alternative.me/fng/?limit=2"
        async with aiohttp.ClientSession() as s:
            r = await s.get(url)
            d = await r.json()
        data = d.get("data", [])
        if not data:
            return {"value": 50, "label": "Neutral", "change": "unknown"}
        current = data[0]
        previous = data[1] if len(data) > 1 else data[0]
        value = int(current.get("value", 50))
        label = current.get("value_classification", "Neutral")
        prev_value = int(previous.get("value", 50))
        change = "improving" if value > prev_value else "deteriorating" if value < prev_value else "unchanged"
        return {
            "value": value,
            "label": label,
            "change": change,
            "interpretation": (
                "extreme fear — potential buy opportunity" if value < 25 else
                "fear — market pessimistic" if value < 45 else
                "neutral" if value < 55 else
                "greed — market optimistic" if value < 75 else
                "extreme greed — potential sell signal"
            )
        }
    except Exception:
        return {"value": 50, "label": "Neutral", "change": "unknown",
                "interpretation": "neutral"}

async def get_economic_calendar() -> list:
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        async with aiohttp.ClientSession() as s:
            r = await s.get(url)
            d = await r.json()
        high_impact = [
            {
                "title": e.get("title", ""),
                "country": e.get("country", ""),
                "date": e.get("date", ""),
                "impact": e.get("impact", ""),
                "forecast": e.get("forecast", ""),
                "previous": e.get("previous", "")
            }
            for e in d
            if e.get("impact", "").lower() == "high"
        ]
        return high_impact[:10]
    except Exception:
        return []

async def get_market_context() -> dict:
    fear_greed = await get_fear_greed()
    calendar = await get_economic_calendar()
    calendar_text = ""
    if calendar:
        calendar_text = "\n".join(
            f"- {e['title']} ({e['country']}) on {e['date']} "
            f"[Forecast: {e['forecast']} | Previous: {e['previous']}]"
            for e in calendar[:5]
        )
    else:
        calendar_text = "No high impact events this week."
    return {
        "fear_greed": fear_greed,
        "calendar": calendar,
        "calendar_text": calendar_text,
        "summary": (
            f"Fear & Greed: {fear_greed['value']}/100 "
            f"({fear_greed['label']}) — {fear_greed['interpretation']}\n"
            f"Trend: {fear_greed['change']}\n\n"
            f"HIGH IMPACT EVENTS THIS WEEK:\n{calendar_text}"
        )
    }