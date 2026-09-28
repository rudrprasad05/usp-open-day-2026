"""Load a local Hugging Face token before importing Hub-dependent libraries."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def configure_hf_token(env_file: Path = DEFAULT_ENV_FILE) -> str:
    """Set HF_TOKEN from .env unless the process already has one.

    Accept both ``HF_TOKEN=hf_...`` and a single bare ``hf_...`` token, so
    existing local files work without being rewritten or printed.
    """
    if os.environ.get("HF_TOKEN"):
        return "environment"
    if not env_file.is_file():
        return "none"

    assigned_token: str | None = None
    bare_token: str | None = None
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "HF_TOKEN":
                value = value.strip()
                if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                    value = value[1:-1]
                assigned_token = value
        elif line.startswith("hf_") and not any(char.isspace() for char in line):
            bare_token = line

    token = assigned_token or bare_token
    if not token:
        return "none"
    os.environ["HF_TOKEN"] = token
    return ".env"
