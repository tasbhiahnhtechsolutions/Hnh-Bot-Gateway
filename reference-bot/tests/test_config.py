import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


def test_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="BOT_SECRET"):
        Settings(_env_file=None)
    with pytest.raises(ValidationError, match="BOT_SECRET"):
        Settings(bot_secret=SecretStr("   "), _env_file=None)


def test_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_SECRET", "whsec_x")
    settings = Settings(_env_file=None)
    assert settings.port == 9000
    assert settings.max_skew_seconds == 300
    assert "whsec_x" not in repr(settings)  # SecretStr hides it from logs and tracebacks
