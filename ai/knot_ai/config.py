"""Env-driven config for knot-ai. Settings prefix: KNOT_AI_."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOT_AI_", case_sensitive=False, extra="ignore")
    knot_url: str = "http://localhost:8000"
    llm_provider: str = "stub"  # "stub" | "anthropic" | "openai"
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-4-20250514"


def get_settings() -> Settings:
    return Settings()
