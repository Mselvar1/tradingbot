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