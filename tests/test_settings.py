"""Tests for persisted Settings + provider resolution (no keychain/network)."""

from __future__ import annotations

import keyring  # installed; we monkeypatch it to avoid touching the real keychain

from tsntool import ai
from tsntool.settings import Settings


def _no_keychain(monkeypatch, stored=None):
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: stored)
    monkeypatch.setattr(keyring, "set_password",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no kc")))
    monkeypatch.setattr(keyring, "delete_password", lambda *a, **k: None)


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    s = Settings(provider="ollama", ollama_model="qwen3.5:latest",
                 ollama_base_url="http://h:1234", anthropic_model="claude-opus-4-8")
    s.save()
    loaded = Settings.load()
    assert loaded.provider == "ollama"
    assert loaded.ollama_model == "qwen3.5:latest"
    assert loaded.ollama_base_url == "http://h:1234"
    assert loaded.anthropic_model == "claude-opus-4-8"
    # settings.json holds only non-secret fields (no api key field)
    import json
    data = json.loads((tmp_path / "settings.json").read_text())
    assert set(data) == {"provider", "anthropic_model", "openrouter_model",
                         "ollama_model", "ollama_base_url"}


def test_api_key_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _no_keychain(monkeypatch, stored=None)  # keychain unavailable → file fallback
    s = Settings()
    assert s.set_api_key("sk-ant-secret") == "file"
    assert s.get_api_key() == "sk-ant-secret"
    assert "config file" in s.key_location()
    kf = tmp_path / "anthropic.key"
    assert (kf.stat().st_mode & 0o777) == 0o600          # locked down
    assert s.set_api_key("") == "cleared"
    assert s.get_api_key() == ""
    assert not kf.exists()


def test_resolve_explicit_ollama(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    _no_keychain(monkeypatch, stored=None)
    s = Settings(provider="ollama", ollama_model="qwen3.5:latest", ollama_base_url="http://x:1")
    p = ai.resolve_provider(s)
    assert p.name == "ollama" and p.model == "qwen3.5:latest" and p.base_url == "http://x:1"


def test_resolve_explicit_anthropic_uses_stored_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    _no_keychain(monkeypatch, stored="sk-stored")  # keychain returns a stored key
    s = Settings(provider="anthropic", anthropic_model="claude-opus-4-8")
    p = ai.resolve_provider(s)
    assert p.name == "anthropic" and p.model == "claude-opus-4-8" and p.api_key == "sk-stored"


def test_resolve_explicit_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # keychain returns a key only for the openrouter account
    monkeypatch.setattr(keyring, "get_password",
                        lambda svc, acct: "sk-or-x" if acct == "openrouter_api_key" else None)
    s = Settings(provider="openrouter", openrouter_model="openai/gpt-4o")
    p = ai.resolve_provider(s)
    assert p.name == "openrouter" and p.model == "openai/gpt-4o" and p.api_key == "sk-or-x"
    assert p.base_url and "openrouter" in p.base_url


def test_resolve_auto_prefers_openrouter_over_ollama(tmp_path, monkeypatch):
    monkeypatch.setenv("TSNTOOL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    p = ai.resolve_provider(Settings(provider="auto"))
    assert p.name == "openrouter"
