"""Tests for AI context assembly and provider detection (no network calls)."""

from __future__ import annotations

from pathlib import Path

from tsntool import ai
from tsntool.analyzer import AnalysisResult, StreamLatency


def test_build_context_empty():
    ctx = ai.build_context()
    assert "No network loaded" in ctx


def test_build_context_with_results():
    s = StreamLatency(module="Net.engineActuator.app[4].sink", count=4,
                      mean_s=120e-6, min_s=100e-6, max_s=126e-6)
    s.deadline_s = 100e-6  # miss
    res = AnalysisResult(sca_path=Path("AutomaticTsn-#0.sca"), streams=[s],
                         total_sent=100, total_received=120)
    ctx = ai.build_context(analysis=res, ini_name="omnetpp.ini")
    assert "engineActuator.app[4]" in ctx
    assert "FAIL" in ctx
    assert "Detected problems" in ctx
    assert "AutomaticTsn-#0.sca" in ctx


def test_detect_forced_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert ai.detect_provider().name == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert ai.detect_provider().name == "ollama"


def test_detect_anthropic_via_key(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    p = ai.detect_provider()
    assert p.name == "anthropic" and p.model == "claude-opus-4-8"


def test_detect_none_when_nothing_available(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ai, "_ollama_reachable", lambda *a, **k: False)
    assert ai.detect_provider() is None


def test_system_preamble_states_simulation_framing():
    # the assistant must never claim provable/guaranteed bounds
    assert "SIMULATION" in ai.SYSTEM_PREAMBLE
    assert "guaranteed" in ai.SYSTEM_PREAMBLE or "provably" in ai.SYSTEM_PREAMBLE
