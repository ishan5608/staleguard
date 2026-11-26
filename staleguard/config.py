"""Runtime configuration, read from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    if not os.getenv("STALEGUARD_NO_DOTENV"):   # the live-demo script sets everything explicitly
        load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # Bifrost gateway (OpenAI-compatible). All LLM traffic goes through it.
    bifrost_url: str = field(default_factory=lambda: os.getenv("BIFROST_URL", "http://localhost:8080"))
    bifrost_virtual_key: str = field(default_factory=lambda: os.getenv("BIFROST_VIRTUAL_KEY", ""))
    # All LLM calls go through Bifrost. Defaults use free tiers: Gemini Flash-Lite for extraction and
    # rewriting, Groq (gpt-oss-120b) for adjudication, each falling back to the other.
    extract_model: str = field(default_factory=lambda: os.getenv("EXTRACT_MODEL", "gemini/gemini-3.5-flash-lite"))
    judge_model: str = field(default_factory=lambda: os.getenv("JUDGE_MODEL", "groq/openai/gpt-oss-120b"))
    # Bifrost tries these, in order, if the primary model fails (the model itself is skipped).
    fallbacks: list[str] = field(default_factory=lambda: _list(
        "LLM_FALLBACKS", "gemini/gemini-3.5-flash-lite,groq/openai/gpt-oss-120b"))

    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    tavily_url: str = field(default_factory=lambda: os.getenv("TAVILY_URL", "https://api.tavily.com"))
    search_depth: str = field(default_factory=lambda: os.getenv("TAVILY_SEARCH_DEPTH", "advanced"))
    results_per_claim: int = field(default_factory=lambda: int(os.getenv("TAVILY_MAX_RESULTS", "5")))

    # Assumed knowledge date of the audited answer when the caller doesn't say.
    default_knowledge_date: str = field(default_factory=lambda: os.getenv("DEFAULT_KNOWLEDGE_DATE", "2024-06"))
    concurrency: int = field(default_factory=lambda: int(os.getenv("STALEGUARD_CONCURRENCY", "4")))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("REQUEST_TIMEOUT", "60")))

    # offline = deterministic heuristics + recorded fixtures, no keys needed.
    mode: str = field(default_factory=lambda: os.getenv("STALEGUARD_MODE", "auto"))

    def resolved_mode(self) -> str:
        if self.mode in ("live", "offline"):
            return self.mode
        return "live" if self.tavily_api_key else "offline"


settings = Settings()
