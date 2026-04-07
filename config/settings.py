from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    telegram_token: str
    anthropic_api_key: str
    newsapi_key: str
    database_url: str
    redis_url: str
    paper_mode: bool = True
    allowed_telegram_ids: str = ""

    @property
    def allowed_ids(self):
        return [int(x.strip()) for x in
                self.allowed_telegram_ids.split(",") if x.strip()]

    class Config:
        env_file = ".env"

settings = Settings()