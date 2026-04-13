import asyncio
import datetime
from services.data.capital import capital_client
from services.data.macro import get_geopolitical_news, get_market_sentiment
from services.data.sentiment import get_market_context
from services.data.news import get_news
from services.risk import risk
from services.signal_history import history
from services.execution.capital_executor import executor
from services.memory import save_signal, get_memory_context
from claude.client import analyse_btc as analyse
from claude.prompts.btc_analysis import BTC_ANALYSIS_PROMPT
from services.rate_limiter import claude_limiter
from config.settings import settings

BTC_EPIC = "BTCUSD"         # used for /positions (order placement)

# Capital.com uses different epic formats across endpoints.
# resolve_btc_epic() finds the working candle epic at startup and caches it.
_BTC_EPIC_CANDIDATES = [
    "BTCUSD",
    "BITCOIN",
    "CS.D.BITCOIN.TODAY.IP",
    "CS.D.BTCUSD.TODAY.IP",
    "BTC",
]
_btc_candle_epic: str | None = None

SCAN_INTERVAL = 300         # scan every 5 minutes
CONFIDENCE_THRESHOLD = 58   # minimum confidence to place a trade
NOTIFY_THRESHOLD = 75       # minimum confidence to send analysis to Telegram
STRONG_VERDICTS = {"STRONG BUY", "STRONG SELL"}   # always notify regardless of score
MIN_SIGNAL_GAP = 600        # 10 minutes between signals (up to ~6/hour in active sessions)
MAX_CANDLES_1M = 150        # 1-minute candles for scalp indicators
MAX_CANDLES_5M = 60         # 5-minute candles for higher-TF context


# ─── Technical Indicator Helpers ──────────────────────────────────────────────

def _calc_ema(prices: list, period: int) -> float:
    if len(prices) < period:
        return round(prices[-1], 2) if prices else 0.0
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 2)


def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period or 0.001
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


def _calc_bb(closes: list, period: int = 20, mult: float = 2.0):
    """Returns (upper, mid, lower)."""
    if len(closes) < period:
        p = closes[-1] if closes else 0
        return p, p, p
    recent = closes[-period:]
    mid = sum(recent) / period
    std = (sum((c - mid) ** 2 for c in recent) / period) ** 0.5
    return round(mid + mult * std, 2), round(mid, 2), round(mid - mult * std, 2)


def _calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(highs) < 2:
        return 0.0
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]))
        for i in range(1, len(highs))
    ]
    return round(sum(trs[-period:]) / min(period, len(trs)), 2)


def _calc_vwap(closes: list, highs: list, lows: list) -> float:
    """Approximate VWAP as mean typical price (no tick volume from Capital.com)."""
    if not closes:
        return 0.0
    typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    return round(sum(typical) / len(typical), 2)


def _detect_fvg(highs: list, lows: list, lookback: int = 40) -> list:
    """Fair Value Gaps: 3-candle imbalances."""
    fvgs = []
    n = min(len(highs), lookback)
    for i in range(2, n):
        bull_gap = lows[i] - highs[i - 2]
        if bull_gap > 0:
            fvgs.append({"type": "bullish", "low": round(highs[i - 2], 2), "high": round(lows[i], 2)})
        bear_gap = lows[i - 2] - highs[i]
        if bear_gap > 0:
            fvgs.append({"type": "bearish", "low": round(highs[i], 2), "high": round(lows[i - 2], 2)})
    return fvgs[:5]


def _detect_equal_levels(highs: list, lows: list, tolerance: float = 0.0006) -> dict:
    """Equal highs / equal lows = liquidity pools that BTC sweeps before reversing."""
    if not highs or not lows:
        return {}
    recent_h = highs[-30:]
    recent_l = lows[-30:]
    max_h = max(recent_h)
    min_l = min(recent_l)
    eq_highs = [h for h in recent_h if abs(h - max_h) / max_h < tolerance]
    eq_lows  = [l for l in recent_l if abs(l - min_l) / min_l < tolerance]
    return {
        "equal_highs": round(sum(eq_highs) / len(eq_highs), 2) if len(eq_highs) >= 2 else None,
        "equal_lows":  round(sum(eq_lows)  / len(eq_lows),  2) if len(eq_lows)  >= 2 else None,
    }


# ─── Session Detection (24/7 BTC) ─────────────────────────────────────────────

def get_btc_session() -> tuple[str, str]:
    """Returns (session_name, priority: high|medium|low|avoid)."""
    now = datetime.datetime.utcnow()
    t = now.hour * 60 + now.minute
    weekend = now.weekday() >= 5

    if 8 * 60 <= t < 10 * 60:
        return "LONDON OPEN", ("medium" if weekend else "high")
    if 10 * 60 <= t < 13 * 60 + 30:
        return "LONDON MID", ("low" if weekend else "medium")
    if 13 * 60 + 30 <= t < 17 * 60:
        return "NY OPEN", ("medium" if weekend else "high")
    if 17 * 60 <= t < 21 * 60:
        return "NY MID", ("low" if weekend else "medium")
    if 21 * 60 <= t or t < 2 * 60:
        return "DEAD ZONE", "avoid"
    return "ASIAN SESSION", ("low" if weekend else "medium")


def should_scan_btc() -> bool:
    _, priority = get_btc_session()
    return priority != "avoid"


# ─── Epic Resolution ──────────────────────────────────────────────────────────

async def resolve_btc_epic() -> str:
    """Find the Capital.com epic that actually serves BTC candle data.

    /markets/BITCOIN works (price snapshot) but /prices/BITCOIN returns 404 —
    Capital.com uses a different epic format for crypto price history.
    We try known candidates first, then fall back to a market search.
    Result is cached for the lifetime of the process.
    """
    global _btc_candle_epic
    if _btc_candle_epic:
        return _btc_candle_epic

    await capital_client.ensure_session()

    # Try known candidates
    for candidate in _BTC_EPIC_CANDIDATES:
        test = await capital_client.get_ohlcv(candidate, "MINUTE", 5)
        if test:
            print(f"BTC: candle epic resolved → {candidate}")
            _btc_candle_epic = candidate
            return candidate

    # Search Capital.com markets for a working bitcoin epic
    print("BTC: searching Capital.com markets for Bitcoin epic...")
    markets = await capital_client.search_market("bitcoin")
    for m in markets:
        epic = m.get("epic", "")
        if not epic:
            continue
        test = await capital_client.get_ohlcv(epic, "MINUTE", 5)
        if test:
            print(f"BTC: candle epic found via search → {epic}")
            _btc_candle_epic = epic
            return epic

    # Last resort — log all market results so we can see what's available
    if markets:
        found = [f"{m.get('epic')} ({m.get('instrumentName', '')})" for m in markets[:10]]
        print(f"BTC: search returned markets but none had candle data: {found}")
    else:
        print("BTC: market search returned no results")

    _btc_candle_epic = "BITCOIN"   # keep trying, log will show the error
    return "BITCOIN"


# ─── Data Fetching ────────────────────────────────────────────────────────────

async def get_btc_data() -> dict:
    try:
        await capital_client.ensure_session()
        candle_epic = await resolve_btc_epic()
        price_data  = await capital_client.get_price(candle_epic)
        candles_1m  = await capital_client.get_ohlcv(candle_epic, "MINUTE",   MAX_CANDLES_1M)
        candles_5m  = await capital_client.get_ohlcv(candle_epic, "MINUTE_5", MAX_CANDLES_5M)

        if not candles_1m or price_data.get("price", 0) == 0:
            return {}

        c1 = candles_1m
        closes_1m = [c["close"] for c in c1]
        highs_1m  = [c["high"]  for c in c1]
        lows_1m   = [c["low"]   for c in c1]

        # Fallback if 5m candles unavailable
        c5 = candles_5m if candles_5m else []
        closes_5m = [c["close"] for c in c5] if c5 else closes_1m[::5]
        highs_5m  = [c["high"]  for c in c5] if c5 else highs_1m[::5]
        lows_5m   = [c["low"]   for c in c5] if c5 else lows_1m[::5]

        current = price_data["price"]

        # ── 1-minute indicators ──
        rsi_1m   = _calc_rsi(closes_1m)
        ema8     = _calc_ema(closes_1m, 8)
        ema21    = _calc_ema(closes_1m, 21)
        ema50    = _calc_ema(closes_1m, 50)
        bb_u, bb_m, bb_l = _calc_bb(closes_1m)
        atr      = _calc_atr(highs_1m, lows_1m, closes_1m)
        vwap     = _calc_vwap(closes_1m[-60:], highs_1m[-60:], lows_1m[-60:])

        # ── 5-minute indicators ──
        rsi_5m   = _calc_rsi(closes_5m)
        ema21_5m = _calc_ema(closes_5m, 21)

        # 5m trend: compare current EMA21 to its value 3 candles ago
        if len(closes_5m) >= 6:
            ema21_5m_prev = _calc_ema(closes_5m[:-3], 21)
            trend_5m = (
                "bullish"  if ema21_5m > ema21_5m_prev and closes_5m[-1] > ema21_5m else
                "bearish"  if ema21_5m < ema21_5m_prev and closes_5m[-1] < ema21_5m else
                "neutral"
            )
        else:
            trend_5m = "neutral"

        # 5m structure: Higher Highs/Higher Lows or Lower Highs/Lower Lows
        if len(highs_5m) >= 6:
            hh = highs_5m[-1] > highs_5m[-3] > highs_5m[-5]
            hl = lows_5m[-1]  > lows_5m[-3]  > lows_5m[-5]
            lh = highs_5m[-1] < highs_5m[-3] < highs_5m[-5]
            ll = lows_5m[-1]  < lows_5m[-3]  < lows_5m[-5]
            structure_5m = "uptrend" if (hh and hl) else "downtrend" if (ll and lh) else "ranging"
        else:
            structure_5m = "unknown"

        # ── Derived context ──
        if ema8 > ema21 > ema50:
            ema_alignment = "bullish_stack"
        elif ema8 < ema21 < ema50:
            ema_alignment = "bearish_stack"
        else:
            ema_alignment = "mixed"

        bb_ctx = (
            "upper_band"  if current >= bb_u else
            "lower_band"  if current <= bb_l else
            "squeeze"     if (bb_u - bb_l) / bb_m < 0.005 else
            "mid"         if abs(current - bb_m) / bb_m < 0.001 else
            "between"
        )

        vwap_pos = "above" if current > vwap * 1.001 else "below" if current < vwap * 0.999 else "at"

        # Volume proxy: recent candle body vs 20-period average body
        avg_body = sum(abs(c["close"] - c["open"]) for c in c1[-20:]) / 20 or 1
        cur_body = abs(c1[-1]["close"] - c1[-1]["open"])
        vol_ratio = round(cur_body / avg_body, 2)
        vol_trend = "high" if vol_ratio > 1.5 else "low" if vol_ratio < 0.7 else "normal"

        session_high = max(highs_1m[-60:]) if len(highs_1m) >= 60 else max(highs_1m)
        session_low  = min(lows_1m[-60:])  if len(lows_1m)  >= 60 else min(lows_1m)

        fvg_zones   = _detect_fvg(highs_1m, lows_1m)
        liq_levels  = _detect_equal_levels(highs_1m, lows_1m)

        prev_close  = closes_1m[0]
        change_pct  = round((current - prev_close) / prev_close * 100, 3)

        # 5-minute price change: current vs close 5 candles ago
        p5m = closes_1m[-6] if len(closes_1m) >= 6 else closes_1m[0]
        price_change_5m = round((current - p5m) / p5m * 100, 3) if p5m else 0.0

        return {
            "ticker": "BTC-USD", "price": current, "prev_close": prev_close,
            "change_pct": change_pct, "price_change_5m": price_change_5m,
            # 1m
            "rsi": rsi_1m, "ema8": ema8, "ema21": ema21, "ema50": ema50,
            "ema_alignment": ema_alignment,
            "bb_upper": bb_u, "bb_mid": bb_m, "bb_lower": bb_l, "bb_context": bb_ctx,
            "vwap": vwap, "vwap_position": vwap_pos,
            "atr": atr, "volume_ratio": vol_ratio, "volume_trend": vol_trend,
            "session_high": session_high, "session_low": session_low,
            "fvg_zones": fvg_zones, "liquidity_levels": liq_levels,
            # 5m
            "rsi_5m": rsi_5m, "ema21_5m": ema21_5m,
            "trend_5m": trend_5m, "structure_5m": structure_5m,
            "day_high": price_data.get("high", session_high),
            "day_low":  price_data.get("low",  session_low),
        }
    except Exception as e:
        print(f"BTC data error: {e}")
        return {}


# ─── Pre-filter (fast check before calling Claude) ────────────────────────────

def has_btc_setup(d: dict) -> bool:
    """Score >= 2 passes to Claude. Keeps Claude calls focused on real setups."""
    score = 0
    rsi       = d.get("rsi", 50)
    ema_align = d.get("ema_alignment", "mixed")
    bb_ctx    = d.get("bb_context", "between")
    vol       = d.get("volume_ratio", 1.0)
    fvgs      = d.get("fvg_zones", [])
    price     = d.get("price", 0)
    liq       = d.get("liquidity_levels", {})

    if ema_align in ("bullish_stack", "bearish_stack"):
        score += 2
    if rsi > 70 or rsi < 30:
        score += 2
    elif rsi > 60 or rsi < 40:
        score += 1
    if bb_ctx in ("upper_band", "lower_band"):
        score += 1
    if bb_ctx == "squeeze":
        score += 1
    if vol > 1.5:
        score += 1
    if price > 0:
        for fvg in fvgs:
            if fvg["low"] <= price <= fvg["high"] * 1.005:
                score += 2
                break
    if liq.get("equal_highs") or liq.get("equal_lows"):
        score += 1

    return score >= 2


# ─── Main Scan ────────────────────────────────────────────────────────────────

async def scan_btc(market_context: dict) -> dict | None:
    if risk.kill_switch:
        return None

    d = await get_btc_data()
    if not d or d.get("price", 0) == 0:
        print("BTC: no data — skipped")
        return None

    session, priority = get_btc_session()
    print(
        f"BTC: ${d['price']:,.0f}  RSI={d['rsi']}  "
        f"EMA={d['ema_alignment']}  BB={d['bb_context']}  "
        f"5m={d['trend_5m']}  session={session}({priority})"
    )

    if not has_btc_setup(d):
        print("BTC: no setup — skipped")
        return None

    # Claude pre-filter: only analyse when conditions are actually extreme
    rsi_extreme    = d["rsi"] < 35 or d["rsi"] > 65
    price_moving   = abs(d.get("price_change_5m", 0)) > 0.5
    if not (rsi_extreme or price_moving):
        print(
            f"BTC: pre-filter skipped "
            f"(RSI={d['rsi']} change5m={d.get('price_change_5m', 0):+.3f}%)"
        )
        return None

    if not await claude_limiter.acquire("BTC"):
        return None

    sentiment = await get_market_sentiment()
    btc_news  = await get_news("bitcoin BTC cryptocurrency crypto", max_articles=5)
    geo_news  = await get_geopolitical_news()

    news_text = "\n".join(
        f"- {a['title']} ({a['source']}, {a['published']})" for a in btc_news
    ) or "No recent BTC news."

    geo_text = "\n".join(
        f"- [{a['category'].upper()}] {a['title']}" for a in geo_news[:4]
    ) or "No geopolitical news."

    fvg_text = "\n".join(
        f"  {f['type'].upper()} FVG: {f['low']:,.2f} – {f['high']:,.2f}"
        for f in d["fvg_zones"][:3]
    ) or "  None detected"

    liq = d["liquidity_levels"]
    liq_text = ""
    if liq.get("equal_highs"):
        liq_text += f"\n  Equal Highs (buy-side liquidity): ${liq['equal_highs']:,.2f}"
    if liq.get("equal_lows"):
        liq_text += f"\n  Equal Lows (sell-side liquidity): ${liq['equal_lows']:,.2f}"
    if not liq_text:
        liq_text = "\n  None detected"

    memory_context = await get_memory_context("BTC-USD")
    fear_greed = market_context.get("fear_greed", {})

    combined_news = (
        f"BTC NEWS:\n{news_text}\n\n"
        f"MACRO & GEOPOLITICAL:\n{geo_text}\n\n"
        f"MARKET REGIME: VIX {sentiment.get('vix', 0)} — {sentiment.get('regime', 'unknown')}\n\n"
        f"HISTORICAL PERFORMANCE:\n{memory_context}"
    )

    prompt = BTC_ANALYSIS_PROMPT.format(
        ticker="BTC-USD (Bitcoin)",
        price=f"{d['price']:,.2f}",
        prev_close=f"{d['prev_close']:,.2f}",
        change_pct=d["change_pct"],
        rsi=d["rsi"],
        ema8=d["ema8"],
        ema21=d["ema21"],
        ema50=d["ema50"],
        ema_alignment=d["ema_alignment"],
        bb_upper=d["bb_upper"],
        bb_mid=d["bb_mid"],
        bb_lower=d["bb_lower"],
        bb_context=d["bb_context"],
        vwap=d["vwap"],
        vwap_position=d["vwap_position"],
        atr=d["atr"],
        volume_ratio=d["volume_ratio"],
        volume_trend=d["volume_trend"],
        session_high=f"{d['session_high']:,.2f}",
        session_low=f"{d['session_low']:,.2f}",
        rsi_5m=d["rsi_5m"],
        ema21_5m=d["ema21_5m"],
        trend_5m=d["trend_5m"],
        structure_5m=d["structure_5m"],
        fvg_zones=fvg_text,
        liquidity_levels=liq_text,
        session=session,
        session_priority=priority,
        sentiment=market_context.get("summary", "No sentiment data."),
        fear_greed=fear_greed.get("value", 50),
        fear_greed_label=fear_greed.get("label", "Neutral"),
        news=combined_news,
    )

    print(f"BTC: analysing with Claude...")
    result = await analyse(prompt)

    if "error" in result:
        print(f"BTC: Claude error — {result}")
        return None

    confidence = result.get("confidence_score", 0)
    action     = result.get("recommended_action", "wait")
    verdict    = result.get("trading_verdict", "WAIT")
    setup      = result.get("setup_type", "?")
    print(f"BTC: {confidence}/100  {verdict}  ({setup})")

    if confidence < CONFIDENCE_THRESHOLD:
        print("BTC: below threshold — skipped")
        return None

    if action not in ("buy", "sell"):
        td = result.get("trend_direction", "neutral")
        action = "buy" if td == "bullish" else "sell"

    return {
        "ticker": "BTC-USD",
        "action": action,
        "confidence": confidence,
        "timeframe": result.get("timeframe", "scalp_15min"),
        "price": d["price"],
        "change_pct": d["change_pct"],
        "rsi": d["rsi"],
        "ema8": d["ema8"], "ema21": d["ema21"], "ema50": d["ema50"],
        "ema_alignment": d["ema_alignment"],
        "bb_upper": d["bb_upper"], "bb_lower": d["bb_lower"],
        "vwap": d["vwap"], "vwap_position": d["vwap_position"],
        "volume_ratio": d["volume_ratio"],
        "trend_5m": d["trend_5m"], "structure_5m": d["structure_5m"],
        "entry": result.get("entry_zone", [d["price"], d["price"]]),
        "entry_trigger": result.get("entry_trigger", "n/a"),
        "stop_loss": result.get("stop_loss", 0),
        "stop_loss_reason": result.get("stop_loss_reason", "n/a"),
        "stop_loss_pct": result.get("stop_loss_pct", "n/a"),
        "tp1": result.get("take_profit_1", "n/a"),
        "tp1_pct": result.get("take_profit_1_pct", "n/a"),
        "tp2": result.get("take_profit_2", "n/a"),
        "tp2_pct": result.get("take_profit_2_pct", "n/a"),
        "tp3": result.get("take_profit_3", "n/a"),
        "tp3_pct": result.get("take_profit_3_pct", "n/a"),
        "rr": result.get("risk_reward", "n/a"),
        "setup_type": result.get("setup_type", "n/a"),
        "market_regime": result.get("market_regime", "unknown"),
        "ema_signal": result.get("ema_signal", "n/a"),
        "bb_signal": result.get("bb_signal", "n/a"),
        "rsi_signal": result.get("rsi_signal", "n/a"),
        "volume_signal": result.get("volume_signal", "n/a"),
        "sentiment_signal": result.get("sentiment_signal", "n/a"),
        "fvg_present": result.get("fvg_present", False),
        "fvg_zone": result.get("fvg_zone"),
        "order_block_present": result.get("order_block_present", False),
        "order_block_zone": result.get("order_block_zone"),
        "liquidity_sweep_detected": result.get("liquidity_sweep_detected", False),
        "bos_detected": result.get("bos_detected", False),
        "choch_detected": result.get("choch_detected", False),
        "market_structure": result.get("market_structure", "unknown"),
        "trading_verdict": verdict,
        "verdict_reason": result.get("verdict_reason", ""),
        "risk_comment": result.get("risk_comment", ""),
        "confluences": result.get("confluences", []),
        "high_impact_event_risk": result.get("high_impact_event_risk", "no"),
        "analysis_summary": result.get("analysis_summary", ""),
        "invalidation": result.get("invalidation", ""),
        "news_catalyst": result.get("news_catalyst", "none"),
        "session_context": session,
        "session_priority": priority,
        "vix": sentiment.get("vix", 0),
        "regime": sentiment.get("regime", "unknown"),
        "fear_greed": fear_greed.get("value", 50),
        "fear_greed_label": fear_greed.get("label", "Neutral"),
    }


# ─── Signal Formatter ─────────────────────────────────────────────────────────

def _p(v) -> str:
    """Format price value safely."""
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v) -> str:
    try:
        return str(v) if isinstance(v, str) else f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


async def format_btc_signal(sig: dict) -> str:
    action = "BUY" if sig.get("action") == "buy" else "SELL"
    verdict = sig.get("trading_verdict", action)
    confs = sig.get("confluences", [])
    conf_text = " | ".join(confs) if confs else "none"

    smc_lines = ""
    if sig.get("fvg_present"):
        smc_lines += f"\nFVG zone: {sig.get('fvg_zone', 'n/a')}"
    if sig.get("order_block_present"):
        smc_lines += f"\nOrder Block: {sig.get('order_block_zone', 'n/a')}"
    if sig.get("liquidity_sweep_detected"):
        smc_lines += "\nLiquidity sweep detected"
    if sig.get("bos_detected"):
        smc_lines += "\nBOS confirmed"
    if sig.get("choch_detected"):
        smc_lines += "\nCHoCH detected"

    event_warn = "\nHIGH IMPACT EVENT — trade with caution" if sig.get("high_impact_event_risk") == "yes" else ""

    change_pct = sig.get("change_pct", 0) or 0
    ema_align  = (sig.get("ema_alignment") or "n/a").replace("_", " ")

    return (
        f"───────────────────\n"
        f"DUTCHALPHA BTC SIGNAL\n"
        f"───────────────────\n"
        f"BTC {action} — {verdict}\n"
        f"{sig.get('verdict_reason', '')}\n\n"
        f"Setup:    {(sig.get('setup_type') or 'n/a').upper().replace('_',' ')}\n"
        f"Regime:   {(sig.get('market_regime') or 'n/a').upper().replace('_',' ')}\n"
        f"Confidence: {sig.get('confidence', 0)}/100\n"
        f"Session: {sig.get('session_context','n/a')} ({sig.get('session_priority','')})\n\n"
        f"MARKET\n"
        f"Price:  {_p(sig.get('price', 0))} ({change_pct:+.3f}%)\n"
        f"RSI(1m): {sig.get('rsi','n/a')} — {sig.get('rsi_signal','n/a')}\n"
        f"EMA 8/21/50: {sig.get('ema8','?')} / {sig.get('ema21','?')} / {sig.get('ema50','?')}\n"
        f"Stack: {ema_align} — {sig.get('ema_signal','n/a')}\n"
        f"BB: {_p(sig.get('bb_lower',0))} / {_p(sig.get('bb_upper',0))} — {sig.get('bb_signal','n/a')}\n"
        f"VWAP: {_p(sig.get('vwap',0))} ({sig.get('vwap_position','n/a')})\n"
        f"5m Trend: {sig.get('trend_5m','n/a')} | Structure: {sig.get('structure_5m','n/a')}\n"
        f"Volume: {sig.get('volume_ratio','n/a')}x — {sig.get('volume_signal','n/a')}\n"
        f"VIX: {sig.get('vix','n/a')} — {sig.get('regime','n/a')}\n"
        f"Fear & Greed: {sig.get('fear_greed','n/a')}/100 ({sig.get('fear_greed_label','n/a')})\n\n"
        f"SMC CONFLUENCES\n"
        f"{conf_text}"
        f"{smc_lines}\n\n"
        f"ENTRY\n"
        f"Zone:    {sig.get('entry','n/a')}\n"
        f"Trigger: {sig.get('entry_trigger','n/a')}\n\n"
        f"RISK MANAGEMENT\n"
        f"Stop:  {_p(sig.get('stop_loss',0))} (-{_pct(sig.get('stop_loss_pct','n/a'))})\n"
        f"Why:   {sig.get('stop_loss_reason','n/a')}\n"
        f"Advice: {sig.get('risk_comment','n/a')}\n\n"
        f"TARGETS\n"
        f"TP1: {_p(sig.get('tp1',0))} (+{_pct(sig.get('tp1_pct','n/a'))}) — close 50%\n"
        f"TP2: {_p(sig.get('tp2',0))} (+{_pct(sig.get('tp2_pct','n/a'))}) — close 30%\n"
        f"TP3: {_p(sig.get('tp3',0))} (+{_pct(sig.get('tp3_pct','n/a'))}) — close 20%\n"
        f"R:R: {sig.get('rr','n/a')}\n\n"
        f"ANALYSIS\n"
        f"Catalyst: {sig.get('news_catalyst','none')}\n"
        f"{sig.get('analysis_summary', sig.get('summary', ''))}\n\n"
        f"INVALIDATION\n"
        f"{sig.get('invalidation','n/a')}"
        f"{event_warn}\n\n"
        f"───────────────────\n"
        f"DutchAlpha — AI BTC Scalping\n"
        f"Demo mode — not real money\n"
        f"Trade smart. Manage risk always.\n"
        f"───────────────────"
    )


# ─── Main Loop ────────────────────────────────────────────────────────────────

async def run_btc_scanner(bot, chat_id: int):
    # Stagger startup so Gold scanner creates the Capital.com session first
    await asyncio.sleep(10)
    print("BTC scalping scanner started (24/7 with dead-zone avoidance)...")
    last_signal_time = None

    while True:
        try:
            if should_scan_btc():
                session, priority = get_btc_session()
                print(f"Scanning BTC ({session} / {priority})...")
                market_context = await get_market_context()

                now = datetime.datetime.utcnow().timestamp()
                if last_signal_time and (now - last_signal_time) < MIN_SIGNAL_GAP:
                    print("BTC: cooldown active — waiting")
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                signal = await scan_btc(market_context)
                if signal:
                    history.save(signal)
                    signal_id = await save_signal(signal)
                    signal["db_id"] = signal_id
                    last_signal_time = now

                    # Only notify Telegram for strong signals — trades execute for all
                    is_strong = (
                        signal["confidence"] >= NOTIFY_THRESHOLD
                        or signal.get("trading_verdict") in STRONG_VERDICTS
                    )
                    if is_strong:
                        msg = await format_btc_signal(signal)
                        await bot.send_message(chat_id=chat_id, text=msg)
                        if settings.public_channel_id:
                            try:
                                await bot.send_message(chat_id=settings.public_channel_id, text=msg)
                            except Exception as e:
                                print(f"BTC channel post failed: {e}")
                        print(f"BTC signal sent (confidence={signal['confidence']}, db_id={signal_id})")
                    else:
                        print(f"BTC signal quiet (confidence={signal['confidence']} < {NOTIFY_THRESHOLD}, db_id={signal_id})")

                    trade_result = await executor.place_trade(signal)
                    if trade_result["status"] == "success":
                        trade = trade_result["trade"]
                        trade_msg = (
                            f"BTC TRADE PLACED\n"
                            f"BTC-USD {signal['action'].upper()}\n"
                            f"Deal ID: {trade['deal_id']}\n"
                            f"Confidence: {signal['confidence']}/100\n"
                            f"Size: {trade['size']} units\n"
                            f"Entry: {_p(trade['entry_price'])}\n"
                            f"Stop:  {_p(trade['stop_loss'])}\n"
                            f"TP:    {_p(trade['take_profit'])}\n"
                            f"Mode:  {settings.capital_mode.upper()}"
                        )
                        await bot.send_message(chat_id=chat_id, text=trade_msg)
                        print(f"BTC trade placed: {trade}")
                    else:
                        print(f"BTC trade blocked: {trade_result.get('reason', '?')}")
            else:
                print("BTC: dead zone (21:00-02:00 UTC) — sleeping")

            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"BTC scanner error: {e}")
            await asyncio.sleep(SCAN_INTERVAL)
