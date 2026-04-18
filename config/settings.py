from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    telegram_token: str
    anthropic_api_key: str
    newsapi_key: str
    t212_api_key: Optional[str] = None
    database_url: Optional[str] = None
    redis_url: Optional[str] = None
    paper_mode: bool = True
    allowed_telegram_ids: str = ""
    public_channel_id: str = ""
    capital_api_key_demo: Optional[str] = None
    capital_api_key_live: Optional[str] = None
    capital_mode: str = "demo"
    capital_email: Optional[str] = None
    capital_password: Optional[str] = None
    # Binance public spot API (no key) — BTC reference volume / book for scanner
    binance_enabled: bool = True
    binance_base_url: str = "https://api.binance.com"
    binance_symbol: str = "BTCUSDT"
    # If True, skip BTC signals when Binance 1m vol << 20m avg (dead tape)
    binance_skip_low_volume: bool = False
    min_binance_volume_ratio: float = 0.12
    # BTC order-flow gate (Binance) — applied before returning a tradeable signal
    # When False, Binance flow is not required to confirm entries (more trades; noisier)
    btc_orderflow_gate_enabled: bool = False
    btc_orderflow_min_volume_ratio: float = 0.45
    btc_orderflow_min_imbalance: float = 0.03
    btc_orderflow_require_fresh_snapshot: bool = False
    # If True, scanner can trade in "low" session priority (still skips "avoid")
    btc_allow_low_priority_sessions: bool = True
    # When True, BTC scanner never sleeps for UTC dead zone / choppy windows / priority "avoid"
    btc_scan_ignore_time_filters: bool = True
    # When True, Gold scanner does not skip first-6-min-of-hour / session-edge choppy windows
    gold_scan_ignore_time_filters: bool = True
    # When False, Gold scanner worker is not started (BTC-focused mode; reduces Claude + bad Gold exposure)
    gold_scanner_enabled: bool = False
    # Capital.com executor: max simultaneous opens (local tracker; increase for high-frequency mode)
    max_open_trades: int = 8
    # Shared Claude budget (entries + trade reviews). Raise if you want ≥~10 BTC trades/hour.
    claude_max_calls_per_hour: int = 120
    # Trade manager: seconds between Claude reviews per open position (lower = more API use)
    trade_review_interval_seconds: int = 600
    # BTC: faster cadence and looser gates (still bounded by claude_limiter)
    btc_scan_interval_seconds: int = 30
    btc_min_signal_gap_seconds: int = 300
    btc_min_confidence: int = 55
    btc_max_confidence_cap: int = 68
    btc_min_confluences: int = 1
    btc_strict_ema_stack: bool = False
    btc_relax_setup_score: bool = True
    btc_relax_prefilter: bool = True
    # Signal platform / validation / dashboard
    signal_platform_enabled: bool = True
    circuit_breaker_sl_streak: int = 8
    circuit_breaker_pause_hours: int = 24
    dashboard_auth_token: Optional[str] = None  # optional Bearer for /api/* and MC POST

    @property
    def allowed_ids(self):
        return [int(x.strip()) for x in
                self.allowed_telegram_ids.split(",") if x.strip()]

    @property
    def capital_api_key(self):
        if self.capital_mode == "live":
            return self.capital_api_key_live
        return self.capital_api_key_demo

    class Config:
        env_file = ".env"

settings = Settings()