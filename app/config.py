"""Server configuration from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIGEON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = Field(
        default="dev-insecure-secret-change-me",
        min_length=32,
        description="Secret for signing JWTs and admin sessions.",
    )
    public_url: str = Field(
        default="http://localhost:8000",
        description="Public server URL (used when generating invite links).",
    )

    db_path: str = Field(
        default="./data/pigeon.sqlite3",
        description="SQLite database file path.",
    )
    exports_dir: str = Field(
        default="./exports",
        description="Directory for JSON configuration exports.",
    )

    jwt_ttl_hours: int = Field(default=24 * 30, ge=1)
    admin_session_ttl_hours: int = Field(default=12, ge=1)
    message_ttl_days: int = Field(default=30, ge=1)
    max_ciphertext_bytes: int = Field(default=64 * 1024, ge=128)

    admin_username: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        insecure_values = {
            "dev-insecure-secret-change-me",
            "change-me-please-use-long-random-string",
            "replace-this-with-output-from-the-command-above",
        }
        if value in insecure_values:
            raise ValueError(
                "PIGEON_SECRET_KEY must be replaced with a random secret"
            )
        return value

    @model_validator(mode="after")
    def _reject_placeholder_admin_password(self) -> "Settings":
        if self.admin_username and self.admin_password == "change-me-too":
            raise ValueError(
                "PIGEON_ADMIN_PASSWORD must be replaced before creating an admin"
            )
        return self

    @property
    def db_url(self) -> str:
        path = Path(self.db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path.as_posix()}"

    @property
    def exports_path(self) -> Path:
        path = Path(self.exports_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
