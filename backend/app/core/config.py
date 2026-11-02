"""Application settings, loaded from environment variables (plan sections 3.1 and 14.1)."""

from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe for the selected environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: AppEnv = AppEnv.DEV
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    redis_url: str = "redis://localhost:6379/0"

    # Fernet key used to encrypt bot/webhook secrets at rest (plan section 5).
    secret_encryption_key: SecretStr = Field(default=SecretStr(""))

    # Plan section 3.1: dev/test only. The app must not start in prod with these on.
    allow_private_bot_urls: bool = False
    enable_mock_bot: bool = False

    # Inbound pipeline limits (plan section 6 and ai-contract.md section 6.4 / 9).
    max_request_body_bytes: int = 40 * 1024 * 1024
    max_bot_response_bytes: int = 1024 * 1024
    bot_timeout_seconds: float = 30.0
    mock_bot_slow_seconds: float = 40.0

    # Plan section 16, point 2: strict by default. A request whose X-WA-Number is missing or is
    # not the vendor's registered number gets 403 NUMBER_MISMATCH. "warn" only logs it (debugging).
    wa_number_mismatch_policy: Literal["warn", "reject"] = "reject"

    # Admin panel (plan section 9.3). Comma-separated IPs/CIDRs; empty means any address.
    # The address is the one the app sees, so behind a proxy it must forward the real IP.
    admin_ip_allowlist: str = ""
    admin_session_hours: int = Field(default=8, ge=1, le=72)

    # Wrong-key attempts allowed per client IP per minute (plan section 4).
    auth_fail_limit_per_minute: int = 20

    @model_validator(mode="after")
    def _check_environment_safety(self) -> Self:
        if self.app_env is AppEnv.PROD:
            if self.allow_private_bot_urls:
                raise ConfigError("ALLOW_PRIVATE_BOT_URLS must be false when APP_ENV=prod")
            if self.enable_mock_bot:
                raise ConfigError("ENABLE_MOCK_BOT must be false when APP_ENV=prod")
        if self.app_env is AppEnv.STAGING and self.allow_private_bot_urls:
            # Plan section 3.1: staging has "localhost off".
            raise ConfigError("ALLOW_PRIVATE_BOT_URLS must be false when APP_ENV=staging")
        if not self.secret_encryption_key.get_secret_value():
            raise ConfigError(
                "SECRET_ENCRYPTION_KEY is required (generate with Fernet.generate_key)"
            )
        return self

    @property
    def admin_cookie_secure(self) -> bool:
        """Admin cookies are Secure everywhere except local dev/test over plain http."""
        return self.app_env in (AppEnv.STAGING, AppEnv.PROD)


@lru_cache
def get_settings() -> Settings:
    return Settings()
