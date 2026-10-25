"""Settings kept between runs: the saved sp_dc cookie and the Spotify login.

Stored as JSON in the user's config folder (%APPDATA%\\canvas2we on Windows,
~/.config/canvas2we elsewhere; override with CANVAS2WE_CONFIG_DIR). The file
holds login secrets, so it's created readable by the current user only where
the OS supports that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def config_dir() -> Path:
    if os.environ.get("CANVAS2WE_CONFIG_DIR"):
        return Path(os.environ["CANVAS2WE_CONFIG_DIR"])
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "canvas2we"


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get(key: str, default=None):
    return load().get(key, default)


def update(**values) -> None:
    """Set keys; a value of None removes the key."""
    data = load()
    for k, v in values.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    save(data)
