from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.data.prices import get_price
from services.data.news import get_news
from claude.client import analyse
from claude.prompts.analysis import ANALYSIS_PROMPT

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /analyze AAPL")
        return
    ticker = ctx.args[0].upper()
    await update.message.reply_text(f"Analysing {ticker}...")
    pd = await get_price(ticker)
    articles = await get_news(ticker)
    news_text = "\n".join(
        f"- {a['title']} ({a['source']}, {a['published']})"
        for a in articles
    ) or "No recent news found."
    prompt = ANALYSIS_PROMPT.format(
        ticker=ticker,
        price=pd["price"],
        prev_close=pd["prev_close"],
        news=news_text
    )
    r = await analyse(prompt)
    if "error" in r:
        await update.message.reply_text(f"Analysis failed: {r}")
        return
    msg = (
        f"*{ticker} Analysis*\n"
        f"Trend: {r.get('trend_direction','n/a')} "
        f"({r.get('trend_strength',0)}/100)\n"
        f"Confidence: {r.get('confidence_score',0)}/100\n"
        f"Action: {r.get('recommended_action','n/a')}\n"
        f"Entry: {r.get('entry_zone','n/a')}\n"
        f"Stop: {r.get('stop_loss','n/a')}\n"
        f"R:R: {r.get('risk_reward','n/a')}\n\n"
        f"{r.get('analysis_summary','')}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /news TSLA")
        return
    topic = " ".join(ctx.args)
    articles = await get_news(topic)
    if not articles:
        await update.message.reply_text("No recent news found.")
        return
    lines = [f"• {a['title']}\n  {a['source']} · {a['published']}"
             for a in articles]
    await update.message.reply_text("\n\n".join(lines))