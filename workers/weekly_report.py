"""
Weekly Report — fires every Monday at 07:00 UTC (≈ 08:00 CET).

Checks every 30 minutes whether it's Monday and past 07:00 UTC.
Sends once per calendar week (tracks last report date in memory).
Also fires immediately on bot restart if Monday 07:00+ has passed
and no report has been sent this week.
"""

import asyncio
import datetime

from services.learning import generate_weekly_report

_last_report_date: datetime.date | None = None


async def run_weekly_report(bot, chat_id: int):
    global _last_report_date
    print("Weekly report scheduler started (checks every 30 minutes)...")

    while True:
        try:
            now = datetime.datetime.utcnow()
            today = now.date()

            # Monday = weekday 0, fire at or after 07:00 UTC
            is_monday  = now.weekday() == 0
            after_time = now.hour >= 7
            not_sent   = _last_report_date != today

            if is_monday and after_time and not_sent:
                print("Weekly report: generating...")
                try:
                    msg = await generate_weekly_report()
                    await bot.send_message(chat_id=chat_id, text=msg)
                    _last_report_date = today
                    print("Weekly report: sent successfully")
                except Exception as e:
                    print(f"Weekly report: send failed — {e}")

        except Exception as e:
            print(f"Weekly report scheduler error: {e}")

        # Check again in 30 minutes — precise enough without burning resources
        await asyncio.sleep(1800)
