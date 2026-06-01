"""AI assistant for TSN results — grounded natural-language Q&A.

The assistant answers questions about the loaded network, the configurations,
and the analyzed results. We inject the structured analysis (topology, per-stream
latency with deadlines, detected problems) as the system context and let the model
answer — no tool-use loop is needed because the relevant data is small and fully
known to the tool.

Two provider backends behind one interface:
  * **Anthropic** (production) — the official ``anthropic`` SDK, model
    ``claude-opus-4-8``, adaptive thinking, streaming, with prompt caching on the
    injected context. Needs ``ANTHROPIC_API_KEY``.
  * **Ollama** (local, key-free) — stdlib HTTP to Ollama's native ``/api/chat``
    (a separate provider, not an OpenAI-compatible shim of Claude).

Provider auto-detection mirrors the OMNeT++ AI-Chat behaviour: prefer an explicit
``LLM_PROVIDER``; else an Anthropic key; else a reachable local Ollama.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from .analyzer import AnalysisResult
from .problems import detect_problems
from .topology import Topology

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OLLAMA_MODEL = "llama3.1:latest"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

SYSTEM_PREAMBLE = (
    "You are a Time-Sensitive Networking (TSN) analyst embedded in a tool that "
    "configures and runs OMNeT++/INET simulations. Help the user understand the "
    "network, the configuration, and the simulation results; diagnose problems; "
    "and propose concrete, actionable fixes (e.g. raise a class's CBS idleSlope, "
    "add a TAS gate slot, change priorities, enable FRER, reduce a source rate).\n"
    "Ground every answer in the DATA below. If the data doesn't contain something, "
    "say so rather than guessing.\n"
    "IMPORTANT: these results come from event-driven SIMULATION — they are observed "
    "values over a finite run, NOT formal worst-case bounds. Never claim a deadline "
    "is 'guaranteed' or 'provably' met; speak in terms of what was observed.\n"
    "Be concise and specific; reference streams by name and cite the numbers."
)


# --- context assembly ------------------------------------------------------
def build_context(topology: Topology | None = None,
                  analysis: AnalysisResult | None = None,
                  configs: list | None = None,
                  ini_name: str | None = None) -> str:
    """Assemble the grounding context (stable, cacheable) for the system prompt."""
    parts: list[str] = []

    if ini_name:
        parts.append(f"## Loaded network\n{ini_name}")

    if configs:
        runnable = ", ".join(c.name for c in configs if getattr(c, "runnable", True))
        parts.append("## Available configurations\n" + (runnable or "(none parsed)"))

    if topology and topology.nodes:
        lines = [f"## Network topology\n{topology.summary()}"]
        sw = [n.name for n in topology.nodes if n.role == "switch"]
        dev = [n.name for n in topology.nodes if n.role == "device"]
        if sw:
            lines.append("switches: " + ", ".join(sw))
        if dev:
            lines.append("devices: " + ", ".join(dev))
        lines.append("links: " + "; ".join(
            f"{l.src}-{l.dst} [{l.bitrate}]" for l in topology.links[:30]))
        parts.append("\n".join(lines))

    if analysis and analysis.ok:
        lines = [f"## Analyzed results ({analysis.sca_path.name})",
                 f"sent {analysis.total_sent}, received {analysis.total_received}, "
                 f"{analysis.total_drops} raw drops",
                 "",
                 "Per-stream end-to-end latency (microseconds):",
                 "stream | count | mean_us | max_us | deadline_us | status"]
        for s in analysis.streams:
            dl = "-" if s.deadline_s is None else f"{s.deadline_s * 1e6:.0f}"
            lines.append(f"{s.label} | {s.count} | {s.mean_us:.1f} | {s.max_us:.1f} | "
                         f"{dl} | {s.status}")
        problems = detect_problems(analysis)
        lines.append("\nDetected problems:")
        for p in problems:
            lines.append(f"- [{p.severity}] {p.title}: {p.detail}"
                         + (f" → {p.advice}" if p.advice else ""))
        parts.append("\n".join(lines))
    elif analysis and not analysis.ok:
        parts.append(f"## Results\n(analysis error: {analysis.error})")

    if not parts:
        return "## DATA\n(No network loaded or results analyzed yet.)"
    return "\n\n".join(parts)


# --- provider detection ----------------------------------------------------
@dataclass
class ProviderInfo:
    name: str            # "anthropic" | "ollama"
    model: str
    label: str           # human-readable, for the UI


def _ollama_reachable(timeout: float = 1.5) -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def detect_provider() -> ProviderInfo | None:
    forced = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if forced == "anthropic" or (not forced and os.environ.get("ANTHROPIC_API_KEY")):
        return ProviderInfo("anthropic",
                            os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
                            "Anthropic (Claude)")
    if forced == "ollama" or _ollama_reachable():
        return ProviderInfo("ollama",
                            os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
                            "Ollama (local)")
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ProviderInfo("anthropic", DEFAULT_ANTHROPIC_MODEL, "Anthropic (Claude)")
    return None


# --- streaming chat --------------------------------------------------------
class AIError(RuntimeError):
    pass


def stream_chat(provider: ProviderInfo, system: str,
                history: list[dict], on_text: Callable[[str], None]) -> str:
    """Stream an assistant reply; calls on_text(chunk) and returns the full text."""
    if provider.name == "anthropic":
        return _stream_anthropic(provider.model, system, history, on_text)
    if provider.name == "ollama":
        return _stream_ollama(provider.model, system, history, on_text)
    raise AIError(f"unknown provider: {provider.name}")


def _stream_anthropic(model, system, history, on_text) -> str:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AIError(f"anthropic SDK not installed: {exc}")
    try:
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    except Exception as exc:  # pragma: no cover
        raise AIError(str(exc))
    collected: list[str] = []
    try:
        with client.messages.stream(
            model=model,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=history,
        ) as stream:
            for text in stream.text_stream:
                collected.append(text)
                on_text(text)
    except anthropic.APIError as exc:
        raise AIError(f"Anthropic API error: {getattr(exc, 'message', str(exc))}")
    except Exception as exc:
        raise AIError(str(exc))
    return "".join(collected)


def _stream_ollama(model, system, history, on_text) -> str:
    messages = [{"role": "system", "content": system}] + history
    payload = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(f"{OLLAMA_BASE}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    collected: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("error"):
                    raise AIError(f"Ollama error: {obj['error']}")
                chunk = obj.get("message", {}).get("content", "")
                if chunk:
                    collected.append(chunk)
                    on_text(chunk)
                if obj.get("done"):
                    break
    except urllib.error.URLError as exc:
        raise AIError(f"cannot reach Ollama at {OLLAMA_BASE}: {exc}")
    return "".join(collected)
