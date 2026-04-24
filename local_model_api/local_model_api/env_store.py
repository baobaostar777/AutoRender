from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_LIB_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV = _LIB_ROOT / ".env"


def get_dotenv_path() -> Path:
    p = os.environ.get("LOCAL_MODEL_API_ENV")
    if p:
        return Path(p).expanduser().resolve()
    return _DEFAULT_ENV


def load_secrets(override: bool = False) -> Path:
    """Load key=value pairs from the local .env into os.environ."""
    path = get_dotenv_path()
    if path.is_file():
        load_dotenv(path, override=override)
    return path


def refresh_environ(override: bool = False) -> None:
    """Alias for re-reading .env (e.g. after you edited the file)."""
    load_secrets(override=override)
