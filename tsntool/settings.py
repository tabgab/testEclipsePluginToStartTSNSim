"""Persisted tool settings — the AI/LLM provider configuration.

Non-secret settings (provider choice, model names, Ollama URL) live in a JSON
file under the config directory. Each provider's API key is stored separately and
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

# provider -> (keychain account, key-file name, env var)
_KEY_INFO = {
    "anthropic": ("anthropic_api_key", "anthropic.key", "ANTHROPIC_API_KEY"),
    "openrouter": ("openrouter_api_key", "openrouter.key", "OPENROUTER_API_KEY"),
}


def config_dir() -> Path:
    env = os.environ.get("TSNTOOL_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".config" / "tsntool"


def _settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    provider: str = "auto"        # "auto" | "anthropic" | "openrouter" | "ollama"
    anthropic_model: str = ""
    openrouter_model: str = ""
    ollama_model: str = ""
    ollama_base_url: str = ""

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

    # --- per-provider API keys (keychain preferred, 0600 file fallback) ---
    def get_api_key(self, provider: str = "anthropic") -> str:
        account = _KEY_INFO[provider][0]
        try:
            import keyring
            val = keyring.get_password(KEYRING_SERVICE, account)
            if val:
                return val
        except Exception:
            pass
        return self._file_key(provider)

    def set_api_key(self, key: str, provider: str = "anthropic") -> str:
        """Store the key. Returns where it went: 'keychain' | 'file' | 'cleared'."""
        key = (key or "").strip()
        if not key:
            self._clear_key(provider)
            return "cleared"
        account = _KEY_INFO[provider][0]
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, account, key)
            self._write_file_key("", provider)   # no stale plaintext copy
            return "keychain"
        except Exception:
            self._write_file_key(key, provider)
            return "file"

    def key_location(self, provider: str = "anthropic") -> str:
        account, _file, env = _KEY_INFO[provider]
        try:
            import keyring
            if keyring.get_password(KEYRING_SERVICE, account):
                return "OS keychain"
        except Exception:
            pass
        if self._file_key(provider):
            return "config file (plaintext, 0600)"
        if os.environ.get(env):
            return f"{env} env var"
        return "not set"

    # --- file-fallback helpers --------------------------------------------
    def _keyfile_path(self, provider: str) -> Path:
        return config_dir() / _KEY_INFO[provider][1]

    def _file_key(self, provider: str) -> str:
        f = self._keyfile_path(provider)
        try:
            return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
        except OSError:
            return ""

    def _write_file_key(self, key: str, provider: str) -> None:
        f = self._keyfile_path(provider)
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

    def _clear_key(self, provider: str) -> None:
        self._write_file_key("", provider)
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, _KEY_INFO[provider][0])
        except Exception:
            pass
