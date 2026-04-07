import asyncio
from services.watchlist import watchlist
from services.data.prices import get_price
from services.data.news import get_news
from services.data.macro import get_geopolitical_news, get_market_sentiment, get_sector_news
from services.risk import risk
from claude.client import analyse, review_signal
from claude.prompts.analysis import ANALYSIS_PROMPT, REVIEW_PROMPT

CONFIDENCE_THRESHOLD = 65
REVIEW_THRESHOLD = 70
CHECK_INTERVAL = 900

async def deep_scan_ticker(ticker: str) -> dict | None:
    if risk.kill_switch:
        return None
    try:
        pd = await get_price(ticker)
        if pd["price"] == 0:
            return None

        sentiment = await get_market_sentiment()
        if sentiment["risk_off"]:
            print(f"Risk-off mode active (VIX {sentiment['vix']}), skipping {ticker}")
            return None

        ticker_news = await get_sector_news(ticker)
        geo_news = await get_geopolitical_news()

        ticker_news_text = "\n".join(
            f"- {a['title']} ({a['source']}, {a['published']})"
            for a in ticker_news
        ) or "No recent news."

        geo_text = "\n".join(
            f"- [{a['category'].upper()}] {a['title']} ({a['source']})"
            for a in geo_news[:8]
        ) or "No geopolitical news."

        combined_news = (
            f"ASSET NEWS:\n{ticker_news_text}\n\n"
            f"MACRO & GEOPOLITICAL:\n{geo_text}\n\n"
            f"MARKET REGIME: VIX {sentiment['vix']} — {sentiment['regime']}"
        )

        prompt = ANALYSIS_PROMPT.format(
            ticker=ticker,
            price=pd["price"],
            prev_close=pd["prev_close"],
            news=combined_news
        )

        print(f"Deep analysing {ticker}...")
        result = await analyse(prompt)

        if "error" in result:
            return None

        confidence = result.get("confidence_score", 0)
        action = result.get("recommended_action", "watch")

        if confidence < CONFIDENCE_THRESHOLD or action not in ["buy", "sell"]:
            print(f"{ticker}: confidence {confidence}/100 action {action} — skipped")
            return None

        print(f"{ticker}: first check passed ({confidence}/100) — running review...")

        review_prompt = REVIEW_PROMPT.format(
            ticker=ticker,
            action=action,
            entry=result.get("entry_zone", "n/a"),
            entry_trigger=result.get("entry_trigger", "n/a"),
            stop_loss=result.get("stop_loss", "n/a"),
            stop_loss_pct=result.get("stop_loss_pct", "n/a"),
            stop_loss_reason=result.get("stop_loss_reason", "n/a"),
            tp1=result.get("take_profit_1", "n/a"),
            tp1_pct=result.get("take_profit_1_pct", "n/a"),
            tp2=result.get("take_profit_2", "n/a"),
            tp2_pct=result.get("take_profit_2_pct", "n/a"),
            tp3=result.get("take_profit_3", "n/a"),
            tp3_pct=result.get("take_profit_3_pct", "n/a"),
            rr=result.get("risk_reward", "n/a"),
            timeframe=result.get("timeframe", "n/a"),
            confidence=confidence,
            summary=result.get("analysis_summary", "n/a"),
            invalidation=result.get("invalidation", "n/a"),
            news_catalyst=result.get("news_catalyst", "n/a"),
            price=pd["price"],
            news=combined_news
        )

        review = await review_signal(review_prompt)

        if "error" in review:
            return None

        if not review.get("approved", False):
            print(f"{ticker}: rejected by reviewer — {review.get('concerns', [])}")
            return None

        final_confidence = review.get("final_confidence", confidence)
        if final_confidence < REVIEW_THRESHOLD:
            print(f"{ticker}: final confidence {final_confidence}/100 too low — skipped")
            return None

        sl = review.get("stop_loss_adjustment") or result.get("stop_loss", "n/a")
        sl_reason = review.get("stop_loss_adjustment_reason") or result.get("stop_loss_reason", "n/a")

        return {
            "ticker": ticker,
            "action": action,
            "confidence": final_confidence,
            "timeframe": result.get("timeframe", "n/a"),
            "entry": result.get("entry_zone", "n/a"),
            "entry_trigger": result.get("entry_trigger", "n/a"),
            "stop_loss": sl,
            "stop_loss_reason": sl_reason,
            "stop_loss_pct": result.get("stop_loss_pct", "n/a"),
            "tp1": result.get("take_profit_1", "n/a"),
            "tp1_pct": result.get("take_profit_1_pct", "n/a"),
            "tp2": result.get("take_profit_2", "n/a"),
            "tp2_pct": result.get("take_profit_2_pct", "n/a"),
            "tp3": result.get("take_profit_3", "n/a"),
            "tp3_pct": result.get("take_profit_3_pct", "n/a"),
            "rr": result.get("risk_reward", "n/a"),
            "summary": result.get("analysis_summary", ""),
            "invalidation": result.get("invalidation", ""),
            "news_catalyst": result.get("news_catalyst", "none"),
            "vix": sentiment["vix"],
            "regime": sentiment["regime"],
            "concerns": review.get("concerns", []),
            "best_entry_time": review.get("best_entry_time", "n/a"),
            "review_summary": review.get("review_summary", "")
        }

    except Exception as e:
        print(f"Scanner error for {ticker}: {e}")
        return None

async def format_signal(signal: dict) -> str:
    action_emoji = "BUY" if signal["action"] == "buy" else "SELL"
    concerns = ""
    if signal["concerns"]:
        concerns = "\nConcerns: " + " | ".join(signal["concerns"])

    return (
        f"SIGNAL — {signal['ticker']} {action_emoji}\n"
        f"Confidence: {signal['confidence']}/100\n"
        f"Timeframe: {signal['timeframe']}\n"
        f"Market: VIX {signal['vix']} — {signal['regime']}\n\n"
        f"ENTRY: {signal['entry']}\n"
        f"Trigger: {signal['entry_trigger']}\n\n"
        f"STOP LOSS: {signal['stop_loss']} (-{signal['stop_loss_pct']}%)\n"
        f"Reason: {signal['stop_loss_reason']}\n\n"
        f"TP1: {signal['tp1']} (+{signal['tp1_pct']}%) — exit 50%\n"
        f"TP2: {signal['tp2']} (+{signal['tp2_pct']}%) — exit 30%\n"
        f"TP3: {signal['tp3']} (+{signal['tp3_pct']}%) — exit 20%\n"
        f"R:R: {signal['rr']}\n\n"
        f"Catalyst: {signal['news_catalyst']}\n\n"
        f"Analysis: {signal['summary']}\n\n"
        f"Invalidation: {signal['invalidation']}\n"
        f"Best entry time: {signal['best_entry_time']}\n"
        f"Reviewer: {signal['review_summary']}"
        f"{concerns}\n\n"
        f"Use /buy {signal['ticker']} [amount] to act."
    )

async def run_scanner(bot, chat_id: int):
    print("Scanner started...")
    while True:
        try:
            tickers = watchlist.get()
            print(f"Scanning {len(tickers)} tickers...")
            for ticker in tickers:
                signal = await deep_scan_ticker(ticker)
                if signal:
                    msg = await format_signal(signal)
                    await bot.send_message(
                        chat_id=chat_id,
                        text=msg
                    )
                await asyncio.sleep(3)
        except Exception as e:
            print(f"Scanner loop error: {e}")
        print(f"Scan complete. Next scan in 15 minutes.")
        await asyncio.sleep(CHECK_INTERVAL)