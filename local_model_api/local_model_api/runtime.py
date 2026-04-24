from __future__ import annotations

import os
from typing import Optional

from .env_store import get_dotenv_path, load_secrets
from .resolve import get_provider, required_env_name


def _get_api_key_for_provider(provider: str) -> Optional[str]:
    load_secrets()
    if provider in ("ollama", "local"):
        return None  # not required

    if provider == "google":
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None

    env_name = required_env_name(provider)
    if not env_name:
        return None
    v = os.environ.get(env_name)
    if v:
        return v
    if provider == "openai" and os.environ.get("AZURE_API_KEY"):
        return os.environ.get("AZURE_API_KEY")
    return None


def get_api_key_for_model(model: str | None) -> tuple[str, str | None]:
    """(provider, key if required and present else None)"""
    load_secrets()
    ovr = os.environ.get("LOCAL_MODEL_API_PROVIDER", "").strip() or None
    prov = get_provider(model, explicit=ovr)
    if prov in ("ollama", "local"):
        return prov, None
    k = _get_api_key_for_provider(prov)
    if k and str(k).strip():
        return prov, k
    return prov, None


def require_key_for_model(model: str | None) -> str:
    """
    Ensure a key exists for the resolved provider; fail with a clear error otherwise.
    Returns the provider id.
    """
    override = os.environ.get("LOCAL_MODEL_API_PROVIDER", "").strip() or None
    prov = get_provider(model, explicit=override)
    if prov in ("ollama", "local"):
        return prov
    key = _get_api_key_for_provider(prov)
    if not key or not str(key).strip():
        need = required_env_name(prov)
        p = get_dotenv_path()
        msg = (
            f"Model {model!r} -> provider {prov!r}, but {need!r} is not set. "
            f"Add it to {p} (see .env.example), or set LOCAL_MODEL_API_ENV to another file."
        )
        raise SystemExit(msg)
    return prov


def aider_cmdline_env(model: str | None) -> dict[str, str]:
    """
    Return extra env to merge when launching aider (e.g. force a single key visible).
    Usually not needed if .env is loaded; useful for tests.
    """
    load_secrets()
    _ = require_key_for_model(model)
    return {}
