"""Launch INET/TSN simulations through the ``inet`` wrapper.

We shell out to ``inet`` (which assembles the correct ``-n``/``-l``/``-x`` flags
from ``$INET_ROOT``) inside a bash invocation that first sources the OMNeT++
``setenv``. This is the sequence verified to run the in-vehicle showcase
headless on this machine.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .environment import OmnetppEnv


@dataclass
class RunSpec:
    ini_path: Path
    config: str
    run: int | None = None           # -r, a specific run of an iteration set
    ui: str = "Cmdenv"               # Cmdenv (headless) or Qtenv (GUI)
    sim_time_limit: str | None = None
    mcp_address: str | None = None   # e.g. "localhost:8765"
    extra_args: list[str] = field(default_factory=list)

    @property
    def sim_dir(self) -> Path:
        return Path(self.ini_path).resolve().parent


class SimulationRunner:
    def __init__(self, env: OmnetppEnv):
        self.env = env
        self.proc: subprocess.Popen | None = None

    def build_argv(self, spec: RunSpec) -> list[str]:
        ini = Path(spec.ini_path).resolve()
        args = ["inet", "-u", spec.ui, "-c", spec.config]
        if spec.run is not None:
            args += ["-r", str(spec.run)]
        if spec.sim_time_limit:
            args.append(f"--sim-time-limit={spec.sim_time_limit}")
        if spec.mcp_address:
            args.append(f"--mcp-server-address={spec.mcp_address}")
        args += list(spec.extra_args)
        args.append(ini.name)
        return args

    def build_bash(self, spec: RunSpec) -> list[str]:
        inner = " ".join(shlex.quote(a) for a in self.build_argv(spec))
        script = (
            f"{self.env.setenv_prefix()}\n"
            f"cd {shlex.quote(str(spec.sim_dir))}\n"
            f"exec {inner}"
        )
        return ["bash", "-c", script]

    def preview(self, spec: RunSpec) -> str:
        """Human-readable preview of the command that will run."""
        return " ".join(self.build_argv(spec))

    def run(self, spec: RunSpec, on_line: Callable[[str], None] | None = None) -> int:
        """Run synchronously, streaming stdout/stderr lines to *on_line*."""
        cmd = self.build_bash(spec)
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if on_line:
                on_line(line.rstrip("\n"))
        self.proc.wait()
        return self.proc.returncode

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def list_result_files(sim_dir: str | Path) -> list[Path]:
    results = Path(sim_dir) / "results"
    if not results.is_dir():
        return []
    return sorted(p for p in results.iterdir() if p.is_file())
