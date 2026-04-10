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

CONFIDENCE_THRESHOLD = 55
REVIEW_THRESHOLD = 60
CHECK_INTERVAL = 900

import datetime

def is_market_hours(ticker: str) -> bool:
    now = datetime.datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    if ticker in ["BTC-USD", "ETH-USD"]:
        return True
    if weekday >= 5:
        return False
    market_open = hour > 13 or (hour == 13 and minute >= 30)
    market_close = hour < 20
    return market_open and market_close

async def has_momentum(pd: dict) -> bool:
    change_pct = abs(pd.get("change_pct", 0))
    volume_ratio = pd.get("volume_ratio", 1)
    rsi = pd.get("rsi", 50)
    if change_pct > 0.3:
        return True
    if rsi > 65 or rsi < 35:
        return True
    if volume_ratio > 1.2:
        return True
    return False

async def deep_scan_ticker(ticker: str, market_context: dict) -> dict | None:
    if risk.kill_switch:
        return None
    if not is_market_hours(ticker):
        print(f"{ticker}: market closed — skipped")
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
    market_structure=result.get("market_structure", "unknown"),
    bos_detected=result.get("bos_detected", False),
    choch_detected=result.get("choch_detected", False),
    fvg_present=result.get("fvg_present", False),
    fvg_zone=result.get("fvg_zone", "none"),
    order_block_present=result.get("order_block_present", False),
    order_block_zone=result.get("order_block_zone", "none"),
    liquidity_sweep_detected=result.get("liquidity_sweep_detected", False),
    session_context=result.get("session_context", "unknown"),
    confluences=result.get("confluences", []),
    trading_verdict=result.get("trading_verdict", "WAIT"),
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
            "trading_verdict": result.get("trading_verdict", "WAIT"),
            "verdict_reason": result.get("verdict_reason", ""),
            "risk_comment": result.get("risk_comment", ""),
            "market_structure": result.get("market_structure", "unknown"),
            "bos_detected": result.get("bos_detected", False),
            "choch_detected": result.get("choch_detected", False),
            "fvg_present": result.get("fvg_present", False),
            "fvg_zone": result.get("fvg_zone", None),
            "order_block_present": result.get("order_block_present", False),
            "order_block_zone": result.get("order_block_zone", None),
            "liquidity_sweep_detected": result.get("liquidity_sweep_detected", False),
            "session_context": result.get("session_context", "unknown"),
            "confluences": result.get("confluences", []),
            "final_verdict": review.get("final_verdict", "TAKE TRADE"),
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
    verdict = signal.get("trading_verdict", action)
    verdict_reason = signal.get("verdict_reason", "")
    risk_comment = signal.get("risk_comment", "")

    confluences = signal.get("confluences", [])
    confluences_text = " | ".join(confluences) if confluences else "none detected"

    smc_text = ""
    if signal.get("fvg_present"):
        smc_text += f"FVG zone: {signal.get('fvg_zone', 'n/a')}\n"
    if signal.get("order_block_present"):
        smc_text += f"Order block: {signal.get('order_block_zone', 'n/a')}\n"
    if signal.get("liquidity_sweep_detected"):
        smc_text += "Liquidity sweep detected\n"
    if signal.get("bos_detected"):
        smc_text += "Break of Structure confirmed\n"
    if signal.get("choch_detected"):
        smc_text += "Change of Character detected\n"

    event_warning = ""
    if signal.get("high_impact_event_risk") == "yes":
        event_warning = "\nHIGH IMPACT EVENT — reduce size or wait"

    concerns_text = ""
    if signal.get("concerns"):
        concerns_text = "\nConcerns: " + " | ".join(signal["concerns"])

    final_verdict = signal.get("final_verdict", "TAKE TRADE")

    return (
        f"───────────────────\n"
        f"DUTCHALPHA SIGNAL\n"
        f"───────────────────\n"
        f"{signal['ticker']} — {verdict}\n"
        f"{verdict_reason}\n\n"
        f"VERDICT: {final_verdict}\n"
        f"Confidence: {signal['confidence']}/100\n"
        f"Timeframe: {signal['timeframe']}\n"
        f"Session: {signal.get('session_context', 'n/a').upper()}\n\n"
        f"MARKET CONDITIONS\n"
        f"Structure: {signal.get('market_structure', 'n/a').upper()}\n"
        f"VIX: {signal['vix']} — {signal['regime']}\n"
        f"Fear & Greed: {signal['fear_greed']}/100 ({signal['fear_greed_label']})\n"
        f"RSI: {signal['rsi']} — {signal['rsi_signal']}\n"
        f"Volume: {signal['volume_ratio']}x — {signal['volume_signal']}\n\n"
        f"SMC CONFLUENCES\n"
        f"{confluences_text}\n"
        f"{smc_text}\n"
        f"ENTRY\n"
        f"Zone: {signal['entry']}\n"
        f"Trigger: {signal['entry_trigger']}\n"
        f"Best time: {signal['best_entry_time']}\n\n"
        f"RISK MANAGEMENT\n"
        f"Stop Loss: {signal['stop_loss']} (-{signal['stop_loss_pct']}%)\n"
        f"Why: {signal['stop_loss_reason']}\n"
        f"Advice: {risk_comment}\n\n"
        f"TARGETS\n"
        f"TP1: {signal['tp1']} (+{signal['tp1_pct']}%) — close 50%\n"
        f"TP2: {signal['tp2']} (+{signal['tp2_pct']}%) — close 30%\n"
        f"TP3: {signal['tp3']} (+{signal['tp3_pct']}%) — close 20%\n"
        f"Risk/Reward: {signal['rr']}\n\n"
        f"ANALYSIS\n"
        f"Catalyst: {signal['news_catalyst']}\n"
        f"{signal['summary']}\n\n"
        f"INVALIDATION\n"
        f"{signal['invalidation']}\n\n"
        f"REVIEWER: {signal['review_summary']}"
        f"{concerns_text}"
        f"{event_warning}\n\n"
        f"Use /buy {signal['ticker']} [amount] to act.\n"
        f"───────────────────\n"
        f"DutchAlpha — AI Trading Signals\n"
        f"Trade smart. Manage risk always.\n"
        f"───────────────────"
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