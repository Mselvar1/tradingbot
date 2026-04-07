import aiohttp
from config.settings import settings

async def get_news(query: str, max_articles: int = 5) -> list:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "sortBy": "publishedAt",
        "pageSize": max_articles,
        "language": "en",
        "apiKey": settings.newsapi_key
    }
    async with aiohttp.ClientSession() as s:
        r = await s.get(url, params=params)
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