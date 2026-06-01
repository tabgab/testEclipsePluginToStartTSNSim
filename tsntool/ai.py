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
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-opus-4.6"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OPENROUTER_BASE = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")

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
    label: str = ""      # human-readable, for the UI
    api_key: str | None = None   # explicit key (Anthropic); None → SDK reads env
    base_url: str | None = None  # explicit base URL (Ollama)


def _ollama_reachable(base: str | None = None, timeout: float = 1.5) -> bool:
    try:
        urllib.request.urlopen(f"{base or OLLAMA_BASE}/api/tags", timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def list_ollama_models(base: str | None = None, timeout: float = 3.0) -> list[str]:
    """Return the model names available on an Ollama server (for the settings UI)."""
    try:
        with urllib.request.urlopen(f"{base or OLLAMA_BASE}/api/tags", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", []) if "name" in m]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def detect_provider() -> ProviderInfo | None:
    """Env-only provider detection (no persisted settings)."""
    return resolve_provider(None)


def resolve_provider(settings=None) -> ProviderInfo | None:
    """Resolve the active provider from explicit Settings, then env, then auto.

    Precedence: an explicit ``settings.provider`` wins; otherwise auto-detect
    (Anthropic if a key is available — from settings, keychain or env — else a
    reachable Ollama). Model/URL come from settings if set, else env, else the
    built-in defaults.
    """
    def s(attr):
        return getattr(settings, attr, "") if settings else ""

    stored_anthropic = settings.get_api_key("anthropic") if settings else ""
    stored_or = settings.get_api_key("openrouter") if settings else ""
    env_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    env_or = os.environ.get("OPENROUTER_API_KEY")
    env_provider = os.environ.get("LLM_PROVIDER", "").lower().strip()
    provider = s("provider") or "auto"

    def anthropic(label):
        model = s("anthropic_model") or os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
        return ProviderInfo("anthropic", model, label, api_key=(stored_anthropic or None))

    def openrouter(label):
        model = s("openrouter_model") or os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        return ProviderInfo("openrouter", model, label,
                            api_key=(stored_or or env_or or None), base_url=OPENROUTER_BASE)

    def ollama(label):
        base = s("ollama_base_url") or OLLAMA_BASE
        model = s("ollama_model") or os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        return ProviderInfo("ollama", model, label, base_url=base)

    if provider == "anthropic":
        return anthropic("Anthropic (Claude)")
    if provider == "openrouter":
        return openrouter("OpenRouter")
    if provider == "ollama":
        return ollama("Ollama (local)")

    # auto: anthropic key → openrouter key → reachable ollama
    if stored_anthropic or env_anthropic or env_provider == "anthropic":
        return anthropic("Anthropic (Claude, auto)")
    if stored_or or env_or or env_provider == "openrouter":
        return openrouter("OpenRouter (auto)")
    base = s("ollama_base_url") or OLLAMA_BASE
    if env_provider == "ollama" or _ollama_reachable(base):
        return ollama("Ollama (local, auto)")
    return None


# --- streaming chat --------------------------------------------------------
class AIError(RuntimeError):
    pass


def test_anthropic(model: str, api_key: str | None = None) -> tuple[bool, str]:
    """Validate an Anthropic key + model without spending output tokens."""
    try:
        import anthropic
    except ImportError:
        return False, "anthropic SDK not installed (pip install 'anthropic')"
    try:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        client.models.retrieve(model)
        return True, f"key valid; model '{model}' available"
    except Exception as exc:
        return False, str(getattr(exc, "message", exc))[:200]


def test_openrouter(model: str, api_key: str | None = None) -> tuple[bool, str]:
    """Validate an OpenRouter key via GET /key (no tokens spent)."""
    if not api_key:
        return False, "no OpenRouter API key set"
    req = urllib.request.Request(f"{OPENROUTER_BASE}/key",
                                 headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            json.loads(r.read().decode("utf-8"))
        return True, "key valid"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} (check the key)"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)[:160]


def stream_chat(provider: ProviderInfo, system: str,
                history: list[dict], on_text: Callable[[str], None]) -> str:
    """Stream an assistant reply; calls on_text(chunk) and returns the full text."""
    if provider.name == "anthropic":
        return _stream_anthropic(provider.model, system, history, on_text, provider.api_key)
    if provider.name == "openrouter":
        return _stream_openrouter(provider.model, system, history, on_text, provider.api_key)
    if provider.name == "ollama":
        return _stream_ollama(provider.model, system, history, on_text, provider.base_url)
    raise AIError(f"unknown provider: {provider.name}")


def _stream_anthropic(model, system, history, on_text, api_key=None) -> str:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AIError(f"anthropic SDK not installed: {exc}")
    try:
        # explicit key when configured; otherwise the SDK reads ANTHROPIC_API_KEY
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
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


def _stream_ollama(model, system, history, on_text, base_url=None) -> str:
    base = base_url or OLLAMA_BASE
    messages = [{"role": "system", "content": system}] + history
    payload = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(f"{base}/api/chat", data=payload,
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
        raise AIError(f"cannot reach Ollama at {base}: {exc}")
    return "".join(collected)


def _stream_openrouter(model, system, history, on_text, api_key=None) -> str:
    """Stream from OpenRouter's OpenAI-compatible /chat/completions (SSE)."""
    if not api_key:
        raise AIError("no OpenRouter API key set (Settings… → OpenRouter, "
                      "or the OPENROUTER_API_KEY env var)")
    messages = [{"role": "system", "content": system}] + history
    payload = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
        "X-Title": "tsntool",
    }
    req = urllib.request.Request(f"{OPENROUTER_BASE}/chat/completions",
                                 data=payload, headers=headers, method="POST")
    collected: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise AIError(f"OpenRouter error: {obj['error']}")
                choices = obj.get("choices") or []
                if choices:
                    chunk = (choices[0].get("delta") or {}).get("content") or ""
                    if chunk:
                        collected.append(chunk)
                        on_text(chunk)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        raise AIError(f"OpenRouter HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise AIError(f"cannot reach OpenRouter: {exc}")
    return "".join(collected)
