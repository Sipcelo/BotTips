from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://valuebot:valuebot_senha@127.0.0.1:5433/valuebot"
    odds_api_key: str = ""
    tennis_api_key: str = ""
    sports_api_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # OpenRouter — contexto qualitativo dos alertas (opcional)
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"

    min_ev: float = 0.04
    min_odd: float = 1.60
    max_odd: float = 4.00
    max_margin: float = 0.10
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.03
    min_stake_amount: float = 3.00   # stake mínimo em reais
    max_stake_amount: float = 5.00   # stake máximo em reais
    poll_interval_seconds: int = 300
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
