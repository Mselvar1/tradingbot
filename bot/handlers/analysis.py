from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.data.prices import get_intraday
from services.data.macro import get_sector_news, get_geopolitical_news, get_market_sentiment
from services.data.sentiment import get_market_context
from claude.client import analyse
from claude.prompts.analysis import ANALYSIS_PROMPT

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /analyze NVDA")
        return
    ticker = ctx.args[0].upper()
    await update.message.reply_text(f"Analysing {ticker}...")
    pd = await get_intraday(ticker)
    ticker_news = await get_sector_news(ticker)
    geo_news = await get_geopolitical_news()
    sentiment = await get_market_sentiment()
    market_context = await get_market_context()
    ticker_news_text = "\n".join(
        f"- {a['title']} ({a['source']}, {a['published']})"
        for a in ticker_news
    ) or "No recent news."
    geo_text = "\n".join(
        f"- [{a['category'].upper()}] {a['title']} ({a['source']})"
        for a in geo_news[:5]
    ) or "No geopolitical news."
    combined_news = (
        f"ASSET NEWS:\n{ticker_news_text}\n\n"
        f"MACRO & GEOPOLITICAL:\n{geo_text}\n\n"
        f"MARKET REGIME: VIX {sentiment['vix']} — {sentiment['regime']}"
    )
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
        prev_day_high=pd.get("prev_day_high", 0),
        prev_day_low=pd.get("prev_day_low", 0),
        atr=pd.get("atr", 0),
        volume_ratio=pd.get("volume_ratio", 1),
        support=pd.get("support", 0),
        resistance=pd.get("resistance", 0),
        sentiment=market_context.get("summary", "No sentiment data."),
        price_narrative="",
        news=combined_news,
        learned_patterns="",
    )
    r = await analyse(prompt)
    if "error" in r:
        await update.message.reply_text(f"Analysis failed: {r}")
        return
    fg = market_context.get("fear_greed", {})
    confluences = r.get("confluences", [])
    confluences_text = " | ".join(confluences) if confluences else "none"
    smc_text = ""
    if r.get("fvg_present"):
        smc_text += f"\nFVG zone: {r.get('fvg_zone','n/a')}"
    if r.get("order_block_present"):
        smc_text += f"\nOrder block: {r.get('order_block_zone','n/a')}"
    if r.get("liquidity_sweep_detected"):
        smc_text += "\nLiquidity sweep detected"
    if r.get("bos_detected"):
        smc_text += "\nBOS confirmed"
    if r.get("choch_detected"):
        smc_text += "\nCHoCH detected"

    msg = (
        f"*{ticker} — {r.get('trading_verdict','n/a')}*\n"
        f"{r.get('verdict_reason','')}\n\n"
        f"Price: {pd.get('price',0)} ({pd.get('change_pct',0):+.2f}%)\n"
        f"RSI: {pd.get('rsi',50)} | Volume: {pd.get('volume_ratio',1)}x\n"
        f"Structure: {r.get('market_structure','n/a').upper()}\n"
        f"Session: {r.get('session_context','n/a').upper()}\n"
        f"Fear & Greed: {fg.get('value',50)}/100 ({fg.get('label','Neutral')})\n"
        f"VIX: {sentiment['vix']} — {sentiment['regime']}\n\n"
        f"Confluences: {confluences_text}"
        f"{smc_text}\n\n"
        f"Confidence: {r.get('confidence_score',0)}/100\n"
        f"Action: {r.get('recommended_action','n/a')}\n"
        f"Timeframe: {r.get('time_horizon','n/a')}\n\n"
        f"Entry: {r.get('entry_zone','n/a')}\n"
        f"Trigger: {r.get('entry_trigger','n/a')}\n\n"
        f"Stop: {r.get('stop_loss','n/a')} (-{r.get('stop_loss_pct','n/a')}%)\n"
        f"Reason: {r.get('stop_loss_reason','n/a')}\n"
        f"Risk advice: {r.get('risk_comment','n/a')}\n\n"
        f"TP1: {r.get('take_profit_1','n/a')} (+{r.get('take_profit_1_pct','n/a')}%)\n"
        f"TP2: {r.get('take_profit_2','n/a')} (+{r.get('take_profit_2_pct','n/a')}%)\n"
        f"TP3: {r.get('take_profit_3','n/a')} (+{r.get('take_profit_3_pct','n/a')}%)\n"
        f"R:R: {r.get('risk_reward','n/a')}\n\n"
        f"Catalyst: {r.get('news_catalyst','none')}\n\n"
        f"{r.get('analysis_summary','')}\n\n"
        f"Invalidation: {r.get('invalidation','n/a')}\n\n"
        f"───────────────────\n"
        f"DutchAlpha — AI Trading Signals\n"
        f"Trade smart. Manage risk always.\n"
        f"───────────────────"
    )
    await update.message.reply_text(msg)

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /news NVDA")
        return
    topic = " ".join(ctx.args)
    from services.data.news import get_news
    articles = await get_news(topic)
    if not articles:
        await update.message.reply_text("No recent news found.")
        return
    lines = [f"• {a['title']}\n  {a['source']} · {a['published']}"
             for a in articles]
    await update.message.reply_text("\n\n".join(lines))