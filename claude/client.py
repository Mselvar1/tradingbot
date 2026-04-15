import anthropic
import json
import re
from config.settings import settings

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _json_candidates(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    fence = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        out.append(fence.group(1))
    any_fence = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if any_fence:
        out.append(any_fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        out.append(text[start:end + 1])
    out.append(text)
    return out


def _cleanup_json_candidate(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```json\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^```\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    buf: list[str] = []
    depth = 0
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            buf.append(ch)
            continue
        if ch == "{":
            depth += 1
            buf.append(ch)
            continue
        if ch == "}":
            if depth > 0:
                depth -= 1
                buf.append(ch)
            continue
        buf.append(ch)

    if depth > 0:
        buf.append("}" * depth)
    return "".join(buf)


def _parse_model_json(text: str) -> dict:
    for cand in _json_candidates(text):
        try:
            return json.loads(cand)
        except Exception:
            pass
        try:
            return json.loads(_cleanup_json_candidate(cand))
        except Exception:
            continue
    return {"error": "parse_failed", "raw": text}

async def analyse(prompt: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    return _parse_model_json(text)

async def analyse_btc(prompt: str) -> dict:
    """BTC scanner uses Haiku — 80% cheaper, fast enough for 1-min scalp signals."""
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    return _parse_model_json(text)

async def review_signal(prompt: str) -> dict:
    msg = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    return _parse_model_json(text)

async def review_trade(prompt: str) -> dict:
    """Trade management review — uses Haiku for speed (called every 5 min per position)."""
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    return _parse_model_json(text)

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
    return _parse_model_json(text)