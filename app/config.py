"""Centralised, environment-driven configuration."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local `.env` file.

    Secret fields use ``SecretStr`` so accidental logs or tracebacks cannot expose
    provider credentials.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AI Link Analysis API"
    app_version: str = __version__
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    docs_enabled: bool = True

    # LLM selection. ``auto`` prefers Gemini, then OpenAI, then deterministic demo mode.
    llm_provider: Literal["auto", "gemini", "openai", "demo"] = "auto"
    enable_llm_fallback: bool = True
    llm_repair_attempts: int = Field(default=1, ge=0, le=1)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=180)
    llm_max_output_tokens: int = Field(default=5000, ge=500, le=20000)

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_temperature: float = Field(default=0.2, ge=0.0, le=1.0)

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_reasoning_effort: Literal["", "none", "low", "medium", "high"] = "none"

    # Website extraction limits.
    fetch_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    fetch_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    max_page_bytes: int = Field(default=2_000_000, ge=100_000, le=10_000_000)
    max_content_chars: int = Field(default=18_000, ge=2_000, le=100_000)
    max_redirects: int = Field(default=3, ge=0, le=10)
    max_internal_links: int = Field(default=40, ge=0, le=200)
    user_agent: str = (
        "Mozilla/5.0 (compatible; AILinkAnalysisBot/1.0; "
        "+https://github.com/your-username/ai-link-analysis)"
    )

    # Cache (in-process; replace with Redis for multi-instance deployments).
    cache_ttl_seconds: int = Field(default=3600, ge=0, le=86_400)
    cache_max_entries: int = Field(default=256, ge=0, le=10_000)

    cors_origins: str = ""

    # Per-client ceiling for the analysis endpoint; protects the provider key
    # when the service is publicly reachable.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=10, ge=1, le=10_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        normalised = value.upper()
        if normalised not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalised

    @field_validator("openai_base_url", "gemini_base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("provider base URLs must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def validate_explicit_provider(self) -> Settings:
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_llm_provider(self) -> Literal["gemini", "openai", "demo"]:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return "demo"

    @property
    def is_demo_mode(self) -> bool:
        return self.resolved_llm_provider == "demo"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
