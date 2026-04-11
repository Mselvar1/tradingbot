import asyncio
import datetime
from services.data.capital import capital_client
from services.data.macro import get_geopolitical_news, get_market_sentiment
from services.data.sentiment import get_market_context
from services.data.news import get_news
from services.risk import risk
from services.signal_history import history
from services.execution.capital_executor import executor
from services.memory import init_db, save_signal, get_memory_context
from claude.client import analyse, review_signal
from claude.prompts.analysis import ANALYSIS_PROMPT, REVIEW_PROMPT
from config.settings import settings

GOLD_EPIC = "GOLD"
SCAN_INTERVAL = 120
CONFIDENCE_THRESHOLD = 30
REVIEW_THRESHOLD = 30
MAX_CANDLES = 100


def is_trading_session() -> bool:
    now = datetime.datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    if weekday >= 5:
        return False
    time_now = hour * 60 + minute
    in_london = (7 * 60) <= time_now <= (16 * 60)
    in_ny = (13 * 60 + 30) <= time_now <= (20 * 60)
    return in_london or in_ny


def get_session_name() -> str:
    now = datetime.datetime.utcnow()
    time_now = now.hour * 60 + now.minute
    if 7 * 60 <= time_now <= 9 * 60:
        return "LONDON OPEN"
    elif 9 * 60 < time_now <= 13 * 60 + 30:
        return "LONDON MID"
    elif 13 * 60 + 30 <= time_now <= 15 * 60 + 30:
        return "NY OPEN"
    elif 15 * 60 + 30 < time_now <= 17 * 60:
        return "LONDON/NY OVERLAP"
    elif 17 * 60 < time_now <= 20 * 60:
        return "NY SESSION"
    else:
        return "OFF HOURS"


async def get_gold_data() -> dict:
    try:
        if not capital_client.session_token:
            await capital_client.create_session()
        price_data = await capital_client.get_price(GOLD_EPIC)
        candles_1m = await capital_client.get_candles(GOLD_EPIC, "MINUTE", MAX_CANDLES)
        if not candles_1m or price_data["price"] == 0:
            return {}
        closes = candles_1m
        gains = [closes[i] - closes[i-1] for i in range(1, len(closes)) if closes[i] > closes[i-1]]
        losses = [closes[i-1] - closes[i] for i in range(1, len(closes)) if closes[i] < closes[i-1]]
        avg_gain = sum(gains[-14:]) / 14 if gains else 0
        avg_loss = sum(losses[-14:]) / 14 if losses else 0.001
        rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)
        ma20 = round(sum(closes[-20:]) / min(20, len(closes)), 2)
        ma50 = round(sum(closes[-50:]) / min(50, len(closes)), 2)
        recent_high = max(closes[-20:])
        recent_low = min(closes[-20:])
        atr = round(recent_high - recent_low, 2)
        prev_close = closes[0]
        current = price_data["price"]
        change_pct = round((current - prev_close) / prev_close * 100, 3)
        fvg_zones = []
        for i in range(2, min(len(closes), 30)):
            gap = closes[i] - closes[i-2]
            if abs(gap) > atr * 0.3:
                fvg_zones.append({
                    "type": "bullish" if gap > 0 else "bearish",
                    "low": min(closes[i-2], closes[i]),
                    "high": max(closes[i-2], closes[i])
                })
        session_high = max(closes[-30:]) if len(closes) >= 30 else recent_high
        session_low = min(closes[-30:]) if len(closes) >= 30 else recent_low
        return {
            "ticker": "GOLD",
            "price": current,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "rsi": rsi,
            "ma20": ma20,
            "ma50": ma50,
            "day_high": price_data.get("high", recent_high),
            "day_low": price_data.get("low", recent_low),
            "atr": atr,
            "volume_ratio": 1.0,
            "support": recent_low,
            "resistance": recent_high,
            "session_high": session_high,
            "session_low": session_low,
            "fvg_zones": fvg_zones[:3],
            "source": "capital.com"
        }
    except Exception as e:
        print(f"Gold data error: {e}")
        return {}


async def has_scalp_setup(pd: dict) -> bool:
    rsi = pd.get("rsi", 50)
    change_pct = abs(pd.get("change_pct", 0))
    atr = pd.get("atr", 0)
    price = pd.get("price", 0)
    if rsi > 65 or rsi < 35:
        return True
    if change_pct > 0.15:
        return True
    if atr > 0 and price > 0 and (atr / price * 100) > 0.1:
        return True
    for fvg in pd.get("fvg_zones", []):
        if fvg["low"] <= price <= fvg["high"]:
            return True
    return False


async def scan_gold(market_context: dict):
    if risk.kill_switch:
        return None

    if not is_trading_session():
        print(f"Gold: outside trading hours ({get_session_name()}) — skipped")
        return None

    pd = await get_gold_data()
    if not pd or pd.get("price", 0) == 0:
        print("Gold: no data — skipped")
        return None

    session = get_session_name()
    print(f"Gold: price={pd['price']} RSI={pd['rsi']} change={pd['change_pct']}% session={session}")

    if not await has_scalp_setup(pd):
        print("Gold: no scalp setup — skipped")
        return None

    sentiment = await get_market_sentiment()
    if sentiment.get("vix", 0) > 35:
        print(f"Gold: VIX too high ({sentiment['vix']}) — skipped")
        return None

    gold_news = await get_news("gold XAUUSD precious metals", max_articles=5)
    geo_news = await get_geopolitical_news()

    news_text = "\n".join(
        f"- {a['title']} ({a['source']}, {a['published']})" for a in gold_news
    ) or "No recent gold news."

    geo_text = "\n".join(
        f"- [{a['category'].upper()}] {a['title']}" for a in geo_news[:5]
    ) or "No geopolitical news."

    fvg_text = ""
    if pd.get("fvg_zones"):
        fvg_text = "Detected FVG zones:\n" + "\n".join(
            f"- {f['type']} FVG: {f['low']:.2f} - {f['high']:.2f}"
            for f in pd["fvg_zones"]
        )

    memory_context = await get_memory_context("GOLD")

    combined_news = (
        f"GOLD NEWS:\n{news_text}\n\n"
        f"MACRO & GEOPOLITICAL:\n{geo_text}\n\n"
        f"MARKET REGIME: VIX {sentiment.get('vix', 0)} — {sentiment.get('regime', 'unknown')}\n\n"
        f"HISTORICAL PERFORMANCE:\n{memory_context}\n\n"
        f"SMC DATA:\n{fvg_text}\n"
        f"Session high: {pd.get('session_high', 0)}\n"
        f"Session low: {pd.get('session_low', 0)}\n"
        f"Current session: {session}"
    )

    sentiment_text = market_context.get("summary", "No sentiment data.")

    prompt = ANALYSIS_PROMPT.format(
        ticker="GOLD (XAUUSD)",
        price=pd["price"],
        prev_close=pd["prev_close"],
        change_pct=pd["change_pct"],
        rsi=pd["rsi"],
        ma20=pd["ma20"],
        ma50=pd["ma50"],
        day_high=pd["day_high"],
        day_low=pd["day_low"],
        atr=pd["atr"],
        volume_ratio=pd["volume_ratio"],
        support=pd["support"],
        resistance=pd["resistance"],
        sentiment=sentiment_text,
        news=combined_news
    )

    print(f"Gold: analysing with Claude (RSI:{pd['rsi']} session:{session})...")
    result = await analyse(prompt)

    if "error" in result:
        print(f"Gold: Claude error — {result}")
        return None

    confidence = result.get("confidence_score", 0)
    action = result.get("recommended_action", "watch")
    trading_verdict = result.get("trading_verdict", "WAIT")
    print(f"Gold: {confidence}/100 {trading_verdict}")

    if confidence < CONFIDENCE_THRESHOLD:
        print("Gold: below threshold — skipped")
        return None

    if action not in ["buy", "sell"]:
        trend = result.get("trend_direction", "neutral")
        action = "buy" if trend == "bullish" else "sell"
        print(f"Gold: forced action to {action} based on trend ({trend})")

    review = {
        "approved": True,
        "final_confidence": confidence,
        "concerns": [],
        "final_verdict": "TAKE TRADE",
        "review_summary": "Demo mode — single check only",
        "best_entry_time": "current session",
        "stop_loss_adjustment": None,
        "stop_loss_adjustment_reason": None
    }

    final_confidence = review.get("final_confidence", confidence)
    sl = review.get("stop_loss_adjustment") or result.get("stop_loss", 0)
    sl_reason = review.get("stop_loss_adjustment_reason") or result.get("stop_loss_reason", "n/a")

    return {
        "ticker": "GOLD",
        "action": action,
        "confidence": final_confidence,
        "timeframe": result.get("timeframe", "intraday"),
        "price": pd["price"],
        "change_pct": pd["change_pct"],
        "rsi": pd["rsi"],
        "volume_ratio": 1.0,
        "entry": result.get("entry_zone", [pd["price"], pd["price"]]),
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
        "volume_signal": "normal",
        "ma_signal": result.get("ma_signal", "n/a"),
        "sentiment_signal": result.get("sentiment_signal", "n/a"),
        "high_impact_event_risk": result.get("high_impact_event_risk", "no"),
        "trading_verdict": trading_verdict,
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
        "session_context": session,
        "confluences": result.get("confluences", []),
        "final_verdict": review.get("final_verdict", "TAKE TRADE"),
        "summary": result.get("analysis_summary", ""),
        "invalidation": result.get("invalidation", ""),
        "news_catalyst": result.get("news_catalyst", "none"),
        "vix": sentiment.get("vix", 0),
        "regime": sentiment.get("regime", "unknown"),
        "fear_greed": market_context.get("fear_greed", {}).get("value", 50),
        "fear_greed_label": market_context.get("fear_greed", {}).get("label", "Neutral"),
        "concerns": review.get("concerns", []),
        "best_entry_time": review.get("best_entry_time", "n/a"),
        "review_summary": review.get("review_summary", "")
    }


async def format_signal(signal: dict) -> str:
    action = "BUY" if signal["action"] == "buy" else "SELL"
    verdict = signal.get("trading_verdict", action)
    confluences = signal.get("confluences", [])
    confluences_text = " | ".join(confluences) if confluences else "none"
    smc_text = ""
    if signal.get("fvg_present"):
        smc_text += f"\nFVG: {signal.get('fvg_zone', 'n/a')}"
    if signal.get("order_block_present"):
        smc_text += f"\nOrder Block: {signal.get('order_block_zone', 'n/a')}"
    if signal.get("liquidity_sweep_detected"):
        smc_text += "\nLiquidity sweep detected"
    if signal.get("bos_detected"):
        smc_text += "\nBOS confirmed"
    if signal.get("choch_detected"):
        smc_text += "\nCHoCH detected"
    event_warning = ""
    if signal.get("high_impact_event_risk") == "yes":
        event_warning = "\nHIGH IMPACT EVENT — trade with caution"
    concerns_text = ""
    if signal.get("concerns"):
        concerns_text = "\nConcerns: " + " | ".join(signal["concerns"])
    return (
        f"───────────────────\n"
        f"DUTCHALPHA GOLD SIGNAL\n"
        f"───────────────────\n"
        f"GOLD {action} — {verdict}\n"
        f"{signal.get('verdict_reason', '')}\n\n"
        f"VERDICT: {signal.get('final_verdict', 'TAKE TRADE')}\n"
        f"Confidence: {signal['confidence']}/100\n"
        f"Session: {signal.get('session_context', 'n/a')}\n\n"
        f"MARKET\n"
        f"Price: {signal['price']} ({signal['change_pct']:+.2f}%)\n"
        f"RSI: {signal['rsi']} — {signal['rsi_signal']}\n"
        f"VIX: {signal['vix']} — {signal['regime']}\n"
        f"Fear & Greed: {signal['fear_greed']}/100 ({signal['fear_greed_label']})\n"
        f"Structure: {signal.get('market_structure', 'n/a').upper()}\n\n"
        f"SMC CONFLUENCES\n"
        f"{confluences_text}"
        f"{smc_text}\n\n"
        f"ENTRY\n"
        f"Zone: {signal['entry']}\n"
        f"Trigger: {signal['entry_trigger']}\n"
        f"Best time: {signal['best_entry_time']}\n\n"
        f"RISK MANAGEMENT\n"
        f"Stop Loss: {signal['stop_loss']} (-{signal['stop_loss_pct']}%)\n"
        f"Why: {signal['stop_loss_reason']}\n"
        f"Advice: {signal.get('risk_comment', 'n/a')}\n\n"
        f"TARGETS\n"
        f"TP1: {signal['tp1']} (+{signal['tp1_pct']}%) — close 50%\n"
        f"TP2: {signal['tp2']} (+{signal['tp2_pct']}%) — close 30%\n"
        f"TP3: {signal['tp3']} (+{signal['tp3_pct']}%) — close 20%\n"
        f"R:R: {signal['rr']}\n\n"
        f"ANALYSIS\n"
        f"Catalyst: {signal['news_catalyst']}\n"
        f"{signal['summary']}\n\n"
        f"INVALIDATION\n"
        f"{signal['invalidation']}\n\n"
        f"REVIEWER: {signal['review_summary']}"
        f"{concerns_text}"
        f"{event_warning}\n\n"
        f"───────────────────\n"
        f"DutchAlpha — AI Gold Scalping\n"
        f"Demo mode — not real money\n"
        f"Trade smart. Manage risk always.\n"
        f"───────────────────"
    )


async def run_scanner(bot, chat_id: int):
    print("Gold scalping scanner started...")
    await init_db()
    last_signal_time = None
    min_signal_gap = 300
    while True:
        try:
            if is_trading_session():
                session = get_session_name()
                print(f"Scanning GOLD ({session})...")
                market_context = await get_market_context()
                print(f"Fear & Greed: {market_context['fear_greed']['value']} — {market_context['fear_greed']['label']}")
                now = datetime.datetime.utcnow().timestamp()
                if last_signal_time and (now - last_signal_time) < min_signal_gap:
                    print("Signal cooldown active — waiting")
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                signal = await scan_gold(market_context)
                if signal:
                    msg = await format_signal(signal)
                    await bot.send_message(chat_id=chat_id, text=msg)
                    if settings.public_channel_id:
                        try:
                            await bot.send_message(
                                chat_id=settings.public_channel_id,
                                text=msg
                            )
                        except Exception as e:
                            print(f"Channel post failed: {e}")
                    history.save(signal)
                    signal_id = await save_signal(signal)
                    signal["db_id"] = signal_id
                    last_signal_time = now
                    print(f"Gold signal sent and saved (db_id: {signal_id})")
                    trade_result = await executor.place_trade(signal)
                    if trade_result["status"] == "success":
                        trade = trade_result["trade"]
                        trade_msg = (
                            f"TRADE PLACED — DEMO\n"
                            f"GOLD {signal['action'].upper()}\n"
                            f"Size: {trade['size']} units\n"
                            f"Entry: {trade['entry_price']}\n"
                            f"Stop: {trade['stop_loss']}\n"
                            f"TP: {trade['take_profit']}\n"
                            f"Mode: {settings.capital_mode.upper()}"
                        )
                        await bot.send_message(chat_id=chat_id, text=trade_msg)
                        print(f"Trade placed: {trade}")
                    else:
                        print(f"Trade blocked: {trade_result['reason']}")
            else:
                print(f"Outside trading hours ({get_session_name()}) — sleeping")
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"Scanner error: {e}")
            await asyncio.sleep(SCAN_INTERVAL)