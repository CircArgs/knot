"""Single source of truth for runtime configuration — pydantic-settings backed.

All env-driven config flows through ``Settings`` (a ``pydantic_settings.BaseSettings``
subclass with prefix ``KNOT_``). No raw ``os.environ.get`` calls anywhere else
in the codebase — modules import ``get_settings()`` and read attributes.

Env vars (all prefixed ``KNOT_``)
---------------------------------
``CONTROL_DSN``       postgres DSN for control + data plane.  Required unless
                      ``DEV_MODE=1``, which falls back to the local
                      docker-compose dev creds.
``DEV_MODE``          ``1`` enables the dev DSN fallback + dev defaults for
                      logging.
``AUTH_DEV_MODE``     ``1`` bypasses ``require_user`` (returns a synthetic
                      ``dev:default`` principal); used for local dev / tests.
``BOOTSTRAP_ADMIN_KEY``  optional raw API key; if set and the users table is
                      empty, an ``admin`` user is seeded at startup.
``LOG_LEVEL``         ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``.
                      Default ``INFO``.
``LOG_FORMAT``        ``json`` / ``text``.  When unset, defaults to ``text``
                      under ``DEV_MODE=1`` and ``json`` otherwise.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_DSN = "postgresql://knot:knot@localhost:5432/knot_control"


class Settings(BaseSettings):
    """All env-driven runtime config in one Pydantic model."""

    model_config = SettingsConfigDict(
        env_prefix="KNOT_",
        case_sensitive=False,
        extra="ignore",
    )

    control_dsn: str | None = None
    dev_mode: bool = False
    auth_dev_mode: bool = False
    bootstrap_admin_key: str | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "text"] | None = None
    data_schema: str = "knot_data"
    user_corrections_source: str = "_user_corrections"

    @property
    def dsn(self) -> str:
        """Postgres DSN for both control plane (``public``) and data plane
        (``knot_data``).  Fails closed when ``KNOT_CONTROL_DSN`` is unset
        unless ``KNOT_DEV_MODE=1`` is also set."""
        if self.control_dsn:
            return self.control_dsn
        if self.dev_mode:
            return _DEV_DEFAULT_DSN
        raise RuntimeError(
            "KNOT_CONTROL_DSN is not set. Set the env var, or set "
            "KNOT_DEV_MODE=1 to fall back to the dev docker-compose default."
        )

    @property
    def effective_log_format(self) -> Literal["json", "text"]:
        """Resolve the explicit value or fall back to the dev/prod default."""
        if self.log_format is not None:
            return self.log_format
        return "text" if self.dev_mode else "json"


def get_settings() -> Settings:
    """Construct a fresh Settings — env vars are re-read on every call.

    Not cached: per-request cost is a single Pydantic instantiation
    (~microseconds), and tests that mutate env between cases get the
    expected behaviour without cache-busting.
    """
    return Settings()


def get_dsn() -> str:
    """Convenience wrapper used by ``knot.db``; matches the historical name
    so call sites read naturally.  Equivalent to ``get_settings().dsn``."""
    return get_settings().dsn
