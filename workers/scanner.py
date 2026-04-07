import asyncio
from services.watchlist import watchlist
from services.data.prices import get_price
from services.data.news import get_news
from services.risk import risk
from claude.client import analyse
from claude.prompts.analysis import ANALYSIS_PROMPT

CONFIDENCE_THRESHOLD = 65
CHECK_INTERVAL = 900

async def scan_ticker(ticker: str) -> dict | None:
    if risk.kill_switch:
        return None
    try:
        pd = await get_price(ticker)
        if pd["price"] == 0:
            return None
        articles = await get_news(ticker, max_articles=3)
        news_text = "\n".join(
            f"- {a['title']} ({a['source']}, {a['published']})"
            for a in articles
        ) or "No recent news."
        prompt = ANALYSIS_PROMPT.format(
            ticker=ticker,
            price=pd["price"],
            prev_close=pd["prev_close"],
            news=news_text
        )
        result = await analyse(prompt)
        if "error" in result:
            return None
        confidence = result.get("confidence_score", 0)
        action = result.get("recommended_action", "watch")
        if confidence >= CONFIDENCE_THRESHOLD and action in ["buy", "sell"]:
            return {
                "ticker": ticker,
                "action": action,
                "confidence": confidence,
                "trend": result.get("trend_direction", "n/a"),
                "entry": result.get("entry_zone", "n/a"),
                "stop": result.get("stop_loss", "n/a"),
                "rr": result.get("risk_reward", "n/a"),
                "summary": result.get("analysis_summary", "")
            }
        return None
    except Exception as e:
        print(f"Scanner error for {ticker}: {e}")
        return None

async def run_scanner(bot, chat_id: int):
    print("Scanner started...")
    while True:
        try:
            tickers = watchlist.get()
            print(f"Scanning {len(tickers)} tickers...")
            for ticker in tickers:
                signal = await scan_ticker(ticker)
                if signal:
                    msg = (
                        f"SIGNAL ALERT\n"
                        f"{signal['ticker']} — {signal['action'].upper()}\n"
                        f"Confidence: {signal['confidence']}/100\n"
                        f"Trend: {signal['trend']}\n"
                        f"Entry: {signal['entry']}\n"
                        f"Stop: {signal['stop']}\n"
                        f"R:R: {signal['rr']}\n\n"
                        f"{signal['summary']}\n\n"
                        f"Use /buy {signal['ticker']} [amount] to act."
                    )
                    await bot.send_message(
                        chat_id=chat_id,
                        text=msg
                    )
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Scanner loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)