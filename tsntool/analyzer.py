"""Load and summarize TSN simulation results.

The heavy lifting (reading .sca/.vec via the native ``omnetpp.scave`` bindings)
runs in a subprocess under a sourced ``setenv`` — see :mod:`tsntool._scave_helper`.
This module shells out to it, parses the JSON, and presents the result as simple
dataclasses: per-stream end-to-end latency (with pass/fail against an editable
deadline), packet drops, and delivery totals.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .environment import OmnetppEnv


@dataclass
class StreamLatency:
    module: str
    count: int
    mean_s: float
    min_s: float
    max_s: float
    jitter_s: float | None = None
    binedges: list[float] | None = None
    binvalues: list[float] | None = None
    deadline_s: float | None = None  # set by the UI; None = no deadline

    @property
    def label(self) -> str:
        # "Net.engineActuator.app[4].sink" -> "engineActuator.app[4]"
        parts = self.module.split(".")
        if len(parts) >= 2:
            short = ".".join(parts[1:])
            return short[:-5] if short.endswith(".sink") else short
        return self.module

    @property
    def mean_us(self) -> float:
        return self.mean_s * 1e6

    @property
    def max_us(self) -> float:
        return self.max_s * 1e6

    @property
    def min_us(self) -> float:
        return self.min_s * 1e6

    @property
    def jitter_us(self) -> float | None:
        return None if self.jitter_s is None else self.jitter_s * 1e6

    @property
    def status(self) -> str:
        if self.deadline_s is None:
            return "—"
        return "PASS" if self.max_s <= self.deadline_s else "FAIL"


@dataclass
class DropInfo:
    module: str
    name: str
    count: int

    @property
    def reason(self) -> str:
        n = self.name[:-6] if self.name.endswith(":count") else self.name
        return n


@dataclass
class AnalysisResult:
    sca_path: Path
    streams: list[StreamLatency] = field(default_factory=list)
    drops: list[DropInfo] = field(default_factory=list)
    total_sent: int = 0
    total_received: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def total_drops(self) -> int:
        return sum(d.count for d in self.drops)


def find_result_scas(ini_path: str | Path, config: str | None = None) -> list[Path]:
    """List result .sca files next to *ini_path*, newest first, optional config filter."""
    results_dir = Path(ini_path).parent / "results"
    if not results_dir.is_dir():
        return []
    scas = [p for p in results_dir.glob("*.sca")]
    if config:
        scas = [p for p in scas if p.name.startswith(config + "-")]
    return sorted(scas, key=lambda p: p.stat().st_mtime, reverse=True)


def _extract_json(text: str) -> dict | None:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def analyze(env: OmnetppEnv, sca_path: str | Path, timeout: float = 180) -> AnalysisResult:
    sca_path = Path(sca_path)
    if not sca_path.is_file():
        return AnalysisResult(sca_path, error=f"no such result file: {sca_path}")
    helper = f"{shlex.quote(sys.executable)} -m tsntool._scave_helper {shlex.quote(str(sca_path))}"
    script = f"{env.setenv_prefix()}\nexec {helper}"
    try:
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return AnalysisResult(sca_path, error="result analysis timed out")
    data = _extract_json(proc.stdout)
    if data is None:
        return AnalysisResult(sca_path, error=(proc.stderr.strip()[-300:] or "no output from scave helper"))
    if "error" in data:
        return AnalysisResult(sca_path, error=data["error"])

    jitter = data.get("jitter", {})
    streams = [
        StreamLatency(
            module=s["module"], count=s.get("count", 0),
            mean_s=s.get("mean", 0.0), min_s=s.get("min", 0.0), max_s=s.get("max", 0.0),
            binedges=s.get("binedges"), binvalues=s.get("binvalues"),
            jitter_s=jitter.get(s["module"]),
        )
        for s in data.get("streams", [])
    ]
    streams.sort(key=lambda s: s.max_s, reverse=True)
    drops = [DropInfo(d["module"], d["name"], d["count"]) for d in data.get("drops", [])]
    return AnalysisResult(
        sca_path=sca_path, streams=streams, drops=drops,
        total_sent=data.get("total_sent", 0), total_received=data.get("total_received", 0),
    )
