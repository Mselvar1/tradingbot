ANALYSIS_PROMPT = """
You are an institutional equity analyst.
Analyse {ticker} and return ONLY valid JSON. No text outside the JSON.

Current price: {price}
Previous close: {prev_close}
Recent news: {news}

Return exactly this structure:
{{
  "ticker": "{ticker}",
  "trend_direction": "bullish or bearish or neutral",
  "trend_strength": 0-100,
  "confidence_score": 0-100,
  "entry_zone": [low, high],
  "stop_loss": price,
  "take_profit_1": price,
  "take_profit_2": price,
  "risk_reward": number,
  "analysis_summary": "2-3 sentence thesis",
  "recommended_action": "buy or sell or watch or avoid",
  "time_horizon": "e.g. 5-15 days",
  "warnings": []
}}
"""