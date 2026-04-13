import anthropic
import json
from config.settings import settings

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

async def analyse(prompt: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return {"error": "parse_failed", "raw": text}

async def analyse_btc(prompt: str) -> dict:
    """BTC scanner uses Haiku — 80% cheaper, fast enough for 1-min scalp signals."""
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return {"error": "parse_failed", "raw": text}

async def review_signal(prompt: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return {"error": "parse_failed", "raw": text}

async def analyse_image(b64: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64
            }},
            {"type": "text", "text":
             "Analyse this trading chart for a day trader. "
             "Maximum hold time is 2 days. "
             "Return ONLY JSON with: "
             "ticker_detected, timeframe_detected, trend, "
             "support_levels (array), resistance_levels (array), "
             "patterns_detected (array), "
             "entry_zone, stop_loss, stop_loss_reason, "
             "take_profit_1, take_profit_2, "
             "suggested_scenarios (array of {scenario, probability}), "
             "confidence_score 0-100, "
             "time_horizon (intraday/1-day/2-day), "
             "unknowns (array)"}
        ]}]
    )
    text = msg.content[0].text
    try:
        return json.loads(text[text.find("{"):text.rfind("}")+1])
    except Exception:
        return {"error": "parse_failed"}