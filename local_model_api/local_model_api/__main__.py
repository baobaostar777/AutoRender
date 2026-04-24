from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from .env_store import get_dotenv_path, load_secrets
from .resolve import get_provider, list_providers, required_env_name
from .runtime import _get_api_key_for_provider, require_key_for_model


def _find_model_in_argv(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _run_info(args: argparse.Namespace) -> int:
    load_secrets()
    m = args.model
    ovr = (os.environ.get("LOCAL_MODEL_API_PROVIDER") or "").strip() or None
    p = get_provider(m, explicit=ovr)
    need = required_env_name(p)
    if p in ("ollama", "local"):
        print(f"model={m!r} -> provider={p} (no API key required)")
        return 0
    k = _get_api_key_for_provider(p)
    status = "set" if k and str(k).strip() else "MISSING"
    print(f"model={m!r} -> provider={p!r} -> key {need!r} is {status}")
    print(f".env: {get_dotenv_path()}")
    return 0 if status == "set" else 1


def _run_aider(args: argparse.Namespace) -> int:
    av = list(args.aider_args or [])
    if av and av[0] == "--":
        av = av[1:]
    model = _find_model_in_argv(av)
    load_secrets(override=True)
    require_key_for_model(model)
    bin_name = "aider.exe" if os.name == "nt" else "aider"
    exe = shutil.which("aider")
    if not exe and os.name == "nt":
        local = os.path.join(os.path.expanduser("~"), r".local\bin\aider.exe")
        if os.path.isfile(local):
            exe = local
    if not exe:
        print("aider is not on PATH. Install it (aider-install) and ensure .local\\bin is on PATH.", file=sys.stderr)
        return 127
    return subprocess.call([exe, *av])


def _run_list_providers(_: argparse.Namespace) -> int:
    for prov, name in list_providers().items():
        if name:
            print(f"  {prov:12} {name}")
        else:
            print(f"  {prov:12} (no key)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Load local_model_api/.env and route API keys by model for aider.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("info", help="Show which provider a model id maps to and if the key is set")
    i.add_argument("model", nargs="?", default=None, help='The same id you would pass to aider --model, e.g. gpt-4o')
    i.set_defaults(_fn=_run_info)

    a = sub.add_parser(
        "aider",
        help="Load secrets then exec aider; put aider options after aider, e.g.  aider  --  --model gpt-4o",
    )
    a.add_argument(
        "aider_args",
        nargs=argparse.REMAINDER,
        help="All arguments to forward to aider. Use -- if your shell would eat flags.",
    )
    a.set_defaults(_fn=_run_aider)

    lp = sub.add_parser("list-providers", help="List known providers and their env var names")
    lp.set_defaults(_fn=_run_list_providers)

    ns = p.parse_args()
    return int(ns._fn(ns))


if __name__ == "__main__":
    raise SystemExit(main())
