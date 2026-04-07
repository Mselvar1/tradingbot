import asyncio, aiohttp

async def test():
    url = 'https://newsapi.org/v2/everything'
    params = {'q': 'NVDA', 'pageSize': 3, 'apiKey': 'cc5b37d27fd947889b239dafd7764d20'}
    async with aiohttp.ClientSession() as s:
        r = await s.get(url, params=params)
        d = await r.json()
    print(d.get('status'))
    print(d.get('totalResults'))
    for a in d.get('articles', [])[:2]:
        print(a['title'])

asyncio.run(test())
