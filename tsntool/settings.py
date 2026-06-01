"""Persisted tool settings — currently the AI/LLM provider configuration.

Non-secret settings (provider choice, model names, Ollama URL) live in a JSON
file under the config directory. The Anthropic API key is stored separately and
securely in the OS keychain via :mod:`keyring` when available, falling back to a
``0600`` file if the keychain can't be used. Nothing secret goes in the JSON.

The config directory is ``$TSNTOOL_CONFIG_DIR`` if set (used by tests), else
``~/.config/tsntool``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

KEYRING_SERVICE = "tsntool"
KEY_ACCOUNT = "anthropic_api_key"


def config_dir() -> Path:
    env = os.environ.get("TSNTOOL_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".config" / "tsntool"


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def _keyfile_path() -> Path:
    return config_dir() / "anthropic.key"


@dataclass
class Settings:
    provider: str = "auto"        # "auto" | "anthropic" | "ollama"
    anthropic_model: str = ""     # blank → default / env
    ollama_model: str = ""        # blank → default / env
    ollama_base_url: str = ""     # blank → default / env

    # --- persistence (non-secret) -----------------------------------------
    @classmethod
    def load(cls) -> "Settings":
        path = _settings_path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        return cls()

    def save(self) -> None:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = _settings_path()
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    # --- API key (keychain preferred, 0600 file fallback) -----------------
    def get_api_key(self) -> str:
        """Return the stored Anthropic key (keychain first, then file)."""
        try:
            import keyring
            val = keyring.get_password(KEYRING_SERVICE, KEY_ACCOUNT)
            if val:
                return val
        except Exception:
            pass
        return self._file_key()

    def set_api_key(self, key: str) -> str:
        """Store the key. Returns where it went: 'keychain' | 'file' | 'cleared'."""
        key = (key or "").strip()
        if not key:
            self._clear_key()
            return "cleared"
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEY_ACCOUNT, key)
            self._write_file_key("")     # don't leave a stale plaintext copy
            return "keychain"
        except Exception:
            self._write_file_key(key)
            return "file"

    def key_location(self) -> str:
        try:
            import keyring
            if keyring.get_password(KEYRING_SERVICE, KEY_ACCOUNT):
                return "OS keychain"
        except Exception:
            pass
        if self._file_key():
            return "config file (plaintext, 0600)"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "ANTHROPIC_API_KEY env var"
        return "not set"

    # --- file-fallback helpers --------------------------------------------
    def _file_key(self) -> str:
        f = _keyfile_path()
        try:
            return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
        except OSError:
            return ""

    def _write_file_key(self, key: str) -> None:
        f = _keyfile_path()
        if not key:
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
            return
        config_dir().mkdir(parents=True, exist_ok=True)
        f.write_text(key, encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass

    def _clear_key(self) -> None:
        self._write_file_key("")
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, KEY_ACCOUNT)
        except Exception:
            pass
