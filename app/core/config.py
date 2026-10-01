"""Settings, read from environment variables or a local .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SUPPORTED_REGIONS = {"us", "eu"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="HAVEN_", extra="ignore")

    # Which identifier packs to switch on. "us" covers HIPAA identifiers,
    # "eu" covers GDPR identifiers common in Ireland and the UK.
    regions: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["us", "eu"])

    # spaCy model used for names and places. en_core_web_lg is the most accurate
    # of the standard models; en_core_web_sm is smaller but misses more.
    spacy_model: str = "en_core_web_lg"

    # Presidio confidence score below which a match is ignored.
    score_threshold: float = 0.4

    # Names or terms that must never be scrubbed (e.g. a practice's own name).
    allow_list: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Upstream providers.
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str = "https://api.openai.com"
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = "https://api.anthropic.com"
    upstream_timeout: float = 120.0

    # Network binding. Keep this on localhost unless you know why you're changing it.
    host: str = "127.0.0.1"
    port: int = 8787

    # Audit log location.
    audit_db_path: str = "haven_audit.db"

    @field_validator("regions", mode="before")
    @classmethod
    def _split_regions(cls, v: object) -> object:
        if isinstance(v, str):
            v = [part.strip().lower() for part in v.split(",") if part.strip()]
        if isinstance(v, list):
            unknown = set(v) - SUPPORTED_REGIONS
            if unknown:
                raise ValueError(f"Unknown region(s): {sorted(unknown)}. Use: {sorted(SUPPORTED_REGIONS)}")
        return v

    @field_validator("allow_list", mode="before")
    @classmethod
    def _split_allow_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
