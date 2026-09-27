"""Configuration safety checks."""

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    "secret",
    [
        "dev-insecure-secret-change-me",
        "change-me-please-use-long-random-string",
        "replace-this-with-output-from-the-command-above",
    ],
)
def test_placeholder_secrets_are_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="secret_key"):
        Settings(_env_file=None, secret_key=secret)


def test_example_admin_password_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SIDEWORD_ADMIN_PASSWORD must be replaced"):
        Settings(
            _env_file=None,
            secret_key="a-secure-enough-test-secret-that-is-long",
            admin_username="admin",
            admin_password="change-me-too",
        )


def test_explicit_development_settings_are_accepted() -> None:
    settings = Settings(
        _env_file=None,
        secret_key="a-secure-enough-test-secret-that-is-long",
        admin_username="admin",
        admin_password="a-local-password",
    )

    assert settings.admin_username == "admin"
