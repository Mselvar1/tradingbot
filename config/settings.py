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