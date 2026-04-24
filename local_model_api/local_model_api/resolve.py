from __future__ import annotations

from typing import Optional

# Provider id -> the primary env var aider / LiteLLM expect for that route.
# "none" = no key (e.g. local ollama with default install).
_PROVIDERS: dict[str, Optional[str]] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "google": "GEMINI_API_KEY",  # also GOOGLE_API_KEY; see get_api_key for lookup order
    "xai": "XAI_API_KEY",
    "ollama": None,
    "local": None,
}

# Ordered: first match wins. Tuple of (needles, provider_id).
# Needle matches if it appears in the lowercased model id (e.g. aider --model value).
_PREFIX_RULES: list[tuple[tuple[str, ...], str]] = [
    (("ollama/", "ollama", "http://", "https://", "local/"), "ollama"),
    (("claude", "anthropic/"), "anthropic"),
    (("openrouter/", "openrouter"), "openrouter"),
    (("groq/", "groq-"), "groq"),
    (("vertex_ai", "vertex", "google/"), "google"),
    (("gemini",), "google"),
    (("openai/", "gpt", "o1", "o3", "o4", "chatgpt", "ft:"), "openai"),
    (("x-ai/", "xai/", "grok"), "xai"),
    (("deepseek",), "deepseek"),
    (("mistral", "mistrall"), "mistral"),  # typo guard
]


def get_provider(model: Optional[str], explicit: str | None = None) -> str:
    """
    Return provider id (e.g. "openai", "anthropic", "ollama").

    `explicit` can be set via env LOCAL_MODEL_API_PROVIDER to skip guessing.
    """
    if explicit:
        e = explicit.strip().lower()
        if e in _PROVIDERS:
            return e
        if e in ("google", "gemini"):
            return "google"

    m = (model or "").strip().lower()
    if not m:
        if explicit and explicit.strip().lower() in _PROVIDERS:
            return explicit.strip().lower()
        return "openai"

    for needles, prov in _PREFIX_RULES:
        if any(n in m for n in needles):
            return prov
    if "/" in m:
        vendor = m.split("/", 1)[0]
        if vendor in _PROVIDERS:
            return vendor
    return "openai"


def required_env_name(provider: str) -> Optional[str]:
    name = _PROVIDERS.get(provider, "OPENAI_API_KEY")
    if provider == "google":
        return "GEMINI_API_KEY"  # or GOOGLE_API_KEY; runtime checks both
    return name


def list_providers() -> dict[str, Optional[str]]:
    return dict(_PROVIDERS)
