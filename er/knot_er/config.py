"""Env-driven config for knot-er. Settings prefix: KNOT_ER_."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOT_ER_", case_sensitive=False, extra="ignore")
    knot_url: str = "http://localhost:8000"
    default_strategy: str = "identifier_passthrough"


def get_settings() -> Settings:
    return Settings()
