"""Settings from environment variables. The bot secret is never in code or git."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The ``whsec_...`` value the gateway gave you for this vendor.
    bot_secret: SecretStr = SecretStr("")
    port: int = Field(default=9000, ge=1, le=65535)
    # Contract section 5.2: requests older or newer than this many seconds are rejected.
    max_skew_seconds: int = Field(default=300, ge=1)

    @field_validator("bot_secret")
    @classmethod
    def _secret_required(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("BOT_SECRET is required (the whsec_... value from the gateway)")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
