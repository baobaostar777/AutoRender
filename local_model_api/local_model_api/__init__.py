"""Local registry for API keys and model → provider resolution (for aider, scripts, etc.)."""

from .env_store import get_dotenv_path, load_secrets, refresh_environ
from .resolve import get_provider, list_providers, required_env_name
from .runtime import aider_cmdline_env, get_api_key_for_model, require_key_for_model

__all__ = [
    "get_dotenv_path",
    "load_secrets",
    "refresh_environ",
    "get_provider",
    "required_env_name",
    "list_providers",
    "aider_cmdline_env",
    "get_api_key_for_model",
    "require_key_for_model",
]
