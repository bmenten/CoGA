import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_reject_insecure_defaults_outside_development() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="change-me",
            POSTGRES_PASSWORD="change-me",
            ADMIN_PASSWORD="change-me",
        )


def test_settings_allow_placeholder_defaults_in_test_env() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
    )

    assert settings.is_development is True
