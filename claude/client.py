import anthropic
import json
from config.settings import settings

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

async def analyse(prompt: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
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
             "Analyse this trading chart. Return ONLY JSON with these fields: "
             "ticker_detected, timeframe_detected, trend, "
             "support_levels (array of numbers), "
             "resistance_levels (array of numbers), "
             "patterns_detected (array of strings), "
             "suggested_scenarios (array of {scenario, probability}), "
             "confidence_score (0-100), "
             "unknowns (array of strings)"}
        ]}]
    )
    text = msg.content[0].text
    try:
        return json.loads(text[text.find("{"):text.rfind("}")+1])
    except Exception:
        return {"error": "parse_failed"}