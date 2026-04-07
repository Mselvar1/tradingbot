from telegram import Update
from config.settings import settings

async def auth_check(update: Update) -> bool:
    uid = update.effective_user.id
    if settings.allowed_ids and uid not in settings.allowed_ids:
        await update.message.reply_text("Unauthorised.")
        return False
    return True