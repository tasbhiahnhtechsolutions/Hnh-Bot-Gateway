import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import AppEnv, ConfigError, Settings


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.DEV,
        "secret_encryption_key": SecretStr(Fernet.generate_key().decode()),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize("env", [AppEnv.DEV, AppEnv.TEST])
def test_dev_and_test_allow_private_urls_and_mock_bot(env: AppEnv) -> None:
    settings = make(app_env=env, allow_private_bot_urls=True, enable_mock_bot=True)
    assert settings.allow_private_bot_urls
    assert settings.enable_mock_bot


def test_prod_rejects_private_bot_urls() -> None:
    with pytest.raises(ConfigError, match="ALLOW_PRIVATE_BOT_URLS"):
        make(app_env=AppEnv.PROD, allow_private_bot_urls=True)


def test_prod_rejects_mock_bot() -> None:
    with pytest.raises(ConfigError, match="ENABLE_MOCK_BOT"):
        make(app_env=AppEnv.PROD, enable_mock_bot=True)


def test_staging_rejects_private_bot_urls_but_allows_mock_bot() -> None:
    with pytest.raises(ConfigError, match="ALLOW_PRIVATE_BOT_URLS"):
        make(app_env=AppEnv.STAGING, allow_private_bot_urls=True)
    assert make(app_env=AppEnv.STAGING, enable_mock_bot=True).enable_mock_bot


def test_prod_with_safe_flags_starts() -> None:
    assert make(app_env=AppEnv.PROD).app_env is AppEnv.PROD


def test_missing_encryption_key_is_an_error() -> None:
    with pytest.raises(ConfigError, match="SECRET_ENCRYPTION_KEY"):
        make(secret_encryption_key=SecretStr(""))


def test_env_vars_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert Settings(_env_file=None).app_env is AppEnv.STAGING
