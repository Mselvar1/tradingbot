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
from services.rate_limiter import claude_limiter
from services.learning import get_dynamic_threshold, get_prompt_injection, register_trade_signal
from services.price_tracker import price_tracker
from services.trade_store import trade_store
from claude.prompts.analysis import ANALYSIS_PROMPT, REVIEW_PROMPT
from config.settings import settings

GOLD_EPIC = "GOLD"
SCAN_INTERVAL = 120
CONFIDENCE_THRESHOLD = 75
REVIEW_THRESHOLD = 75
MAX_CANDLES = 100


def is_trading_session() -> bool:
    """
    Gold scans only during the two highest-quality windows:
    - London open: 07:00-08:30 UTC
    - NY open:     13:30-15:00 UTC
    """
    now = datetime.datetime.utcnow()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    london_open = 7 * 60 <= t < 8 * 60 + 30
    ny_open     = 13 * 60 + 30 <= t < 15 * 60
    return london_open or ny_open


def get_session_name() -> str:
    now = datetime.datetime.utcnow()
    if now.weekday() >= 5:
        return "WEEKEND"
    t = now.hour * 60 + now.minute
    if 7 * 60 <= t < 8 * 60 + 30:
        return "LONDON OPEN"
    elif 8 * 60 + 30 <= t < 13 * 60 + 30:
        return "LONDON MID"
    elif 13 * 60 + 30 <= t < 15 * 60:
        return "NY OPEN"
    elif 15 * 60 <= t < 21 * 60:
        return "NY MID"
    elif 2 * 60 <= t < 7 * 60:
        return "ASIAN SESSION"
    else:
        return "OFF HOURS"


def is_session_choppy_window() -> bool:
    """
    Skip first minutes of each UTC hour (0–5) and the last 15 minutes
    of each Gold session window (London / NY).
    """
    now = datetime.datetime.utcnow()
    if now.minute < 6:
        return True
    t = now.hour * 60 + now.minute
    # London open 07:00–08:30 UTC — last 15m: 08:15–08:30
    if 7 * 60 <= t < 8 * 60 + 30:
        if t >= 8 * 60 + 15:
            return True
    # NY open 13:30–15:00 UTC — last 15m: 14:45–15:00
    if 13 * 60 + 30 <= t < 15 * 60:
        if t >= 14 * 60 + 45:
            return True
    return False


async def get_gold_data() -> dict:
    try:
        await capital_client.ensure_session()
        price_data = await capital_client.get_price(GOLD_EPIC)
        candles_1m = await capital_client.get_candles(GOLD_EPIC, "MINUTE", MAX_CANDLES)
        if not candles_1m or price_data["price"] == 0:
            return {}
        bid = float(price_data.get("bid") or 0)
        offer = float(price_data.get("offer") or 0)
        spread = round(offer - bid, 3) if bid and offer else 0.0
        daily = await capital_client.get_ohlcv(GOLD_EPIC, "DAY", 5)
        prev_day_high = 0.0
        prev_day_low = 0.0
        if len(daily) >= 2:
            prev = daily[-2]
            prev_day_high = round(float(prev.get("high") or 0), 2)
            prev_day_low = round(float(prev.get("low") or 0), 2)
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
            "spread": spread,
            "prev_day_high": prev_day_high,
            "prev_day_low": prev_day_low,
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
    fvg_zones = pd.get("fvg_zones", [])
    score = 0
    if rsi > 68 or rsi < 32:
        score += 2
    elif rsi > 60 or rsi < 40:
        score += 1
    if change_pct > 0.4:
        score += 2
    elif change_pct > 0.2:
        score += 1
    if atr > 0 and price > 0 and (atr / price * 100) > 0.12:
        score += 1
    for fvg in fvg_zones:
        if fvg["low"] <= price <= fvg["high"]:
            score += 2
            break
    return score >= 2


async def scan_gold(market_context: dict):
    if risk.kill_switch:
        return None

    from services.signal_platform.circuit_breaker import is_paused

    if await is_paused():
        print("Gold: circuit breaker pause — skipped")
        return None

    if not getattr(settings, "gold_scan_ignore_time_filters", True) and is_session_choppy_window():
        print("Gold: session edge / first 6 UTC minutes — skipped")
        return None

    pd = await get_gold_data()
    if not pd or pd.get("price", 0) == 0:
        print("Gold: no data — skipped")
        return None

    if pd.get("spread", 0) > 0.8:
        print(f"Gold: spread {pd['spread']} > 0.8 — skipped")
        return None

    session = get_session_name()
    print(f"Gold: price={pd['price']} RSI={pd['rsi']} change={pd['change_pct']}% session={session}")

    if not await has_scalp_setup(pd):
        print("Gold: no scalp setup — skipped")
        return None

    # Claude pre-filter: only analyse when RSI is extreme or ATR is elevated
    rsi      = pd["rsi"]
    atr_pct  = (pd["atr"] / pd["price"] * 100) if pd["price"] else 0
    rsi_extreme = rsi < 40 or rsi > 60
    atr_high    = atr_pct > 0.12
    if not (rsi_extreme or atr_high):
        print(
            f"Gold: pre-filter skipped "
            f"(RSI={rsi} ATR%={atr_pct:.3f}%)"
        )
        return None

    if not await claude_limiter.acquire("GOLD"):
        return None

    # Dynamic threshold: may be lowered/raised based on session win rates
    threshold = await get_dynamic_threshold("GOLD", session)
    if threshold != CONFIDENCE_THRESHOLD:
        print(f"Gold: dynamic threshold {threshold} (default {CONFIDENCE_THRESHOLD})")

    learned_patterns = await get_prompt_injection("GOLD")

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
    live_narrative = price_tracker.get_narrative("GOLD")

    combined_news = (
        f"GOLD NEWS:\n{news_text}\n\n"
        f"MACRO & GEOPOLITICAL:\n{geo_text}\n\n"
        f"MARKET REGIME: VIX {sentiment.get('vix', 0)} — {sentiment.get('regime', 'unknown')}\n\n"
        f"HISTORICAL PERFORMANCE:\n{memory_context}\n\n"
        f"SMC DATA:\n{fvg_text}\n"
        f"Session high: {pd.get('session_high', 0)}\n"
        f"Session low: {pd.get('session_low', 0)}\n"
        f"Previous day high: {pd.get('prev_day_high', 0)}\n"
        f"Previous day low: {pd.get('prev_day_low', 0)}\n"
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
        prev_day_high=pd.get("prev_day_high", 0),
        prev_day_low=pd.get("prev_day_low", 0),
        atr=pd["atr"],
        volume_ratio=pd["volume_ratio"],
        support=pd["support"],
        resistance=pd["resistance"],
        sentiment=sentiment_text,
        price_narrative=live_narrative,
        news=combined_news,
        learned_patterns=learned_patterns,
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

    if confidence < threshold:
        print(f"Gold: {confidence} below threshold {threshold} — skipped")
        return None

    if action not in ["buy", "sell"]:
        trend = result.get("trend_direction", "neutral")
        action = "buy" if trend == "bullish" else "sell"
        print(f"Gold: forced action to {action} based on trend ({trend})")

    # MA regime: only BUY when MA20 > MA50; otherwise only SELL
    ma20 = pd.get("ma20", 0)
    ma50 = pd.get("ma50", 0)
    if ma20 and ma50:
        if ma20 > ma50 and action != "buy":
            print(f"Gold: only longs when MA20 ({ma20}) > MA50 ({ma50}) — got {action}, skipped")
            return None
        if ma20 <= ma50 and action != "sell":
            print(f"Gold: only shorts when MA20 ({ma20}) <= MA50 ({ma50}) — got {action}, skipped")
            return None

    # Minimum 2 SMC confluences required
    confluences = result.get("confluences", [])
    if len(confluences) < 2:
        print(f"Gold: only {len(confluences)} confluence(s) — minimum 2 required, skipped")
        return None

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
        f"🎯 GOLD {action} SIGNAL\n"
        f"Confidence: {signal['confidence']}/100 | Session: {signal.get('session_context', 'n/a')}\n"
        f"Entry: {signal['price']} | SL: {signal['stop_loss']} | TP1: {signal['tp1']}\n"
        f"Confluences: {confluences_text}\n"
        f"R:R: {signal['rr']} | Trend: {signal.get('market_structure', 'n/a').upper()}\n"
        f"VIX: {signal['vix']} | F&G: {signal['fear_greed']}/100 ({signal['fear_greed_label']})\n"
        f"{smc_text}"
        f"\n{signal.get('summary', '')}"
        f"\nInvalidation: {signal.get('invalidation', 'n/a')}"
        f"{event_warning}"
        f"{concerns_text}"
    )


async def run_scanner(bot, chat_id: int):
    print("Gold scalping scanner started...")
    await init_db()
    last_signal_time = None
    min_signal_gap = 600
    while True:
        try:
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
                        f"✅ TRADE PLACED — {settings.capital_mode.upper()}\n"
                        f"GOLD {signal['action'].upper()} @ {trade['entry_price']}\n"
                        f"Size: {trade['size']} | SL: {trade['stop_loss']} | TP: {trade['take_profit']}"
                    )
                    await bot.send_message(chat_id=chat_id, text=trade_msg)
                    print(f"Trade placed: {trade}")
                    # Register for self-learning
                    register_trade_signal(
                        deal_id         = trade["deal_id"],
                        signal_id       = signal.get("db_id", 0),
                        ticker          = "GOLD",
                        rsi             = signal.get("rsi", 0),
                        trend_direction = signal.get("trading_verdict", ""),
                        confluences     = signal.get("confluences", []),
                        session         = signal.get("session_context", session),
                    )
                    # Register for trade management
                    trade_store.register(
                        deal_id         = trade["deal_id"],
                        signal          = signal,
                        trade           = trade,
                        entry_narrative = price_tracker.get_narrative("GOLD"),
                    )
                else:
                    print(f"Trade blocked: {trade_result['reason']}")
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"Scanner error: {e}")
            await asyncio.sleep(SCAN_INTERVAL)