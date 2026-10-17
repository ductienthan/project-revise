import json
import os
from pathlib import Path

import keyring
from keyring.errors import KeyringError

SERVICE = "preflight"


def config_dir() -> Path:
    path = Path(os.environ.get("PREFLIGHT_CONFIG_DIR", Path.home() / ".config" / "preflight"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _use_keyring() -> bool:
    return os.environ.get("PREFLIGHT_NO_KEYRING") != "1"


def _file() -> Path:
    return config_dir() / "credentials.json"


def load(api_url: str) -> dict | None:
    if _use_keyring():
        try:
            raw = keyring.get_password(SERVICE, api_url)
            return json.loads(raw) if raw else None
        except KeyringError:
            pass
    path = _file()
    if not path.exists():
        return None
    return json.loads(path.read_text()).get(api_url)


def save(api_url: str, tokens: dict) -> None:
    if _use_keyring():
        try:
            keyring.set_password(SERVICE, api_url, json.dumps(tokens))
            return
        except KeyringError:
            pass
    path = _file()
    data = json.loads(path.read_text()) if path.exists() else {}
    data[api_url] = tokens
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def clear(api_url: str) -> None:
    if _use_keyring():
        try:
            keyring.delete_password(SERVICE, api_url)
        except KeyringError:
            pass
    path = _file()
    if path.exists():
        data = json.loads(path.read_text())
        data.pop(api_url, None)
        path.write_text(json.dumps(data))
