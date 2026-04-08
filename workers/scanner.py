import asyncio
from services.watchlist import watchlist
from services.data.prices import get_price, get_intraday
from services.data.news import get_news
from services.data.macro import get_geopolitical_news, get_market_sentiment, get_sector_news
from services.data.sentiment import get_market_context
from services.risk import risk
from services.signal_history import history
from claude.client import analyse, review_signal
from claude.prompts.analysis import ANALYSIS_PROMPT, REVIEW_PROMPT

CONFIDENCE_THRESHOLD = 65
REVIEW_THRESHOLD = 70
CHECK_INTERVAL = 900

async def has_momentum(pd: dict) -> bool:
    change_pct = abs(pd.get("change_pct", 0))
    volume_ratio = pd.get("volume_ratio", 1)
    rsi = pd.get("rsi", 50)
    if change_pct > 1.0:
        return True
    if change_pct > 0.5 and volume_ratio > 1.3:
        return True
    if rsi > 72 or rsi < 28:
        return True
    return False

async def deep_scan_ticker(ticker: str, market_context: dict) -> dict | None:
    if risk.kill_switch:
        return None
    try:
        pd = await get_intraday(ticker)
        if pd.get("price", 0) == 0:
            return None

        if not await has_momentum(pd):
            print(f"{ticker}: no momentum (change:{pd.get('change_pct',0)}% RSI:{pd.get('rsi',50)}) — skipped")
            return None

        sentiment = await get_market_sentiment()
        if sentiment["risk_off"]:
            print(f"Risk-off (VIX {sentiment['vix']}), skipping {ticker}")
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

        sentiment_text = market_context.get("summary", "No sentiment data.")

        prompt = ANALYSIS_PROMPT.format(
            ticker=ticker,
            price=pd.get("price", 0),
            prev_close=pd.get("prev_close", 0),
            change_pct=pd.get("change_pct", 0),
            rsi=pd.get("rsi", 50),
            ma20=pd.get("ma20", 0),
            ma50=pd.get("ma50", 0),
            day_high=pd.get("day_high", 0),
            day_low=pd.get("day_low", 0),
            atr=pd.get("atr", 0),
            volume_ratio=pd.get("volume_ratio", 1),
            support=pd.get("support", 0),
            resistance=pd.get("resistance", 0),
            sentiment=sentiment_text,
            news=combined_news
        )

        print(f"Deep analysing {ticker} (RSI:{pd.get('rsi',50)} change:{pd.get('change_pct',0)}%)...")
        result = await analyse(prompt)

        if "error" in result:
            return None

        confidence = result.get("confidence_score", 0)
        action = result.get("recommended_action", "watch")

        if confidence < CONFIDENCE_THRESHOLD or action not in ["buy", "sell"]:
            print(f"{ticker}: {confidence}/100 {action} — skipped")
            return None

        print(f"{ticker}: first check passed ({confidence}/100) — reviewing...")

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
            price=pd.get("price", 0),
            rsi=pd.get("rsi", 50),
            volume_ratio=pd.get("volume_ratio", 1),
            sentiment=sentiment_text,
            news=combined_news
        )

        review = await review_signal(review_prompt)

        if "error" in review:
            return None

        if not review.get("approved", False):
            print(f"{ticker}: rejected — {review.get('concerns', [])}")
            return None

        final_confidence = review.get("final_confidence", confidence)
        if final_confidence < REVIEW_THRESHOLD:
            print(f"{ticker}: final confidence {final_confidence}/100 — skipped")
            return None

        sl = review.get("stop_loss_adjustment") or result.get("stop_loss", "n/a")
        sl_reason = (review.get("stop_loss_adjustment_reason")
                     or result.get("stop_loss_reason", "n/a"))

        return {
            "ticker": ticker,
            "action": action,
            "confidence": final_confidence,
            "timeframe": result.get("timeframe", "n/a"),
            "price": pd.get("price", 0),
            "change_pct": pd.get("change_pct", 0),
            "rsi": pd.get("rsi", 50),
            "volume_ratio": pd.get("volume_ratio", 1),
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
            "rsi_signal": result.get("rsi_signal", "n/a"),
            "volume_signal": result.get("volume_signal", "n/a"),
            "ma_signal": result.get("ma_signal", "n/a"),
            "sentiment_signal": result.get("sentiment_signal", "n/a"),
            "high_impact_event_risk": result.get("high_impact_event_risk", "no"),
            "summary": result.get("analysis_summary", ""),
            "invalidation": result.get("invalidation", ""),
            "news_catalyst": result.get("news_catalyst", "none"),
            "vix": sentiment["vix"],
            "regime": sentiment["regime"],
            "fear_greed": market_context.get("fear_greed", {}).get("value", 50),
            "fear_greed_label": market_context.get("fear_greed", {}).get("label", "Neutral"),
            "concerns": review.get("concerns", []),
            "best_entry_time": review.get("best_entry_time", "n/a"),
            "review_summary": review.get("review_summary", "")
        }

    except Exception as e:
        print(f"Scanner error for {ticker}: {e}")
        return None

async def format_signal(signal: dict) -> str:
    action = "BUY" if signal["action"] == "buy" else "SELL"
    concerns = ""
    if signal["concerns"]:
        concerns = "\nConcerns: " + " | ".join(signal["concerns"])
    event_warning = ""
    if signal.get("high_impact_event_risk") == "yes":
        event_warning = "\nHIGH IMPACT EVENT RISK — check calendar before entry"
    return (
        f"SIGNAL — {signal['ticker']} {action}\n"
        f"Confidence: {signal['confidence']}/100\n"
        f"Timeframe: {signal['timeframe']}\n"
        f"Market: VIX {signal['vix']} — {signal['regime']}\n"
        f"Fear & Greed: {signal['fear_greed']}/100 ({signal['fear_greed_label']})\n\n"
        f"PRICE: {signal['price']} ({signal['change_pct']:+.2f}%)\n"
        f"RSI: {signal['rsi']} — {signal['rsi_signal']}\n"
        f"Volume: {signal['volume_ratio']}x — {signal['volume_signal']}\n"
        f"MA signal: {signal['ma_signal']}\n"
        f"Sentiment: {signal['sentiment_signal']}\n\n"
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
        f"Best entry: {signal['best_entry_time']}\n"
        f"Review: {signal['review_summary']}"
        f"{concerns}"
        f"{event_warning}\n\n"
        f"Use /buy {signal['ticker']} [amount] to act."
    )

async def run_scanner(bot, chat_id: int):
    print("Scanner started...")
    while True:
        try:
            tickers = watchlist.get()
            print(f"Scanning {len(tickers)} tickers...")
            market_context = await get_market_context()
            print(f"Fear & Greed: {market_context['fear_greed']['value']} — {market_context['fear_greed']['label']}")
            for ticker in tickers:
                signal = await deep_scan_ticker(ticker, market_context)
                if signal:
                    msg = await format_signal(signal)
                    await bot.send_message(chat_id=chat_id, text=msg)
                    history.save(signal)
                    print(f"Signal saved: {signal['ticker']} {signal['action']}")
                await asyncio.sleep(3)
        except Exception as e:
            print(f"Scanner loop error: {e}")
        print("Scan complete. Next scan in 15 minutes.")
        await asyncio.sleep(CHECK_INTERVAL)