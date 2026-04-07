import base64
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from claude.client import analyse_image

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    photo = update.message.photo[-1]
    await update.message.reply_text("Analysing chart...")
    file = await ctx.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()
    b64 = base64.standard_b64encode(img_bytes).decode()
    r = await analyse_image(b64)
    if "error" in r:
        await update.message.reply_text("Could not analyse image.")
        return
    patterns = ", ".join(r.get("patterns_detected", [])) or "none detected"
    scenarios = "\n".join(
        f"  • {s['scenario']} ({s['probability']})"
        for s in r.get("suggested_scenarios", [])
    )
    msg = (
        f"*Chart Analysis*\n"
        f"Ticker: {r.get('ticker_detected', 'unknown')}\n"
        f"Timeframe: {r.get('timeframe_detected', 'unknown')}\n"
        f"Trend: {r.get('trend', 'n/a')}\n"
        f"Support: {r.get('support_levels', [])}\n"
        f"Resistance: {r.get('resistance_levels', [])}\n"
        f"Patterns: {patterns}\n"
        f"Confidence: {r.get('confidence_score', 0)}/100\n\n"
        f"Scenarios:\n{scenarios}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown") 