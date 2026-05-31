"""Locate and describe the OMNeT++ / INET installation.

The tool drives simulations through INET's ``bin/inet`` wrapper, which needs:
  * the OMNeT++ ``bin/`` on PATH (for ``opp_run_dbg`` etc.) and the rest of the
    environment that ``setenv`` sets up (library paths, NEDPATH bits), and
  * ``$INET_ROOT`` pointing at the INET tree (the wrapper reads
    ``$INET_ROOT/.nedfolders`` and ``.nedexclusions`` and the built library).

Rather than re-implement ``setenv`` in Python, we build a small bash prefix
that sources it and exports ``INET_ROOT`` — exactly the sequence verified to
work on this machine.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


class EnvironmentError(RuntimeError):
    """Raised when a usable OMNeT++/INET installation cannot be located."""


def _looks_like_omnetpp(path: Path) -> bool:
    return (path / "setenv").is_file() and (path / "bin" / "opp_run").exists()


def _find_omnetpp_root(start: Path | None = None) -> Path | None:
    # 1) explicit environment variable
    env = os.environ.get("OMNETPP_ROOT")
    if env and _looks_like_omnetpp(Path(env)):
        return Path(env)
    # 2) walk upward from this file and from the cwd (the tool usually lives
    #    inside the OMNeT++ tree, e.g. <omnetpp>/tsn-tool/)
    candidates = []
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    if start is not None:
        candidates.extend(Path(start).resolve().parents)
    candidates.append(Path.cwd())
    candidates.extend(Path.cwd().parents)
    for cand in candidates:
        if _looks_like_omnetpp(cand):
            return cand
    return None


def _find_inet_root(omnetpp_root: Path) -> Path | None:
    env = os.environ.get("INET_ROOT")
    if env and (Path(env) / "Version").exists():
        return Path(env)
    samples = omnetpp_root / "samples"
    if samples.is_dir():
        # prefer the newest inet-* directory that has a built library
        inets = sorted(samples.glob("inet*"), reverse=True)
        for inet in inets:
            if (inet / "src").is_dir():
                return inet
    return None


@dataclass(frozen=True)
class OmnetppEnv:
    """A resolved OMNeT++ + INET installation."""

    omnetpp_root: Path
    inet_root: Path

    # --- introspection -----------------------------------------------------
    @property
    def version(self) -> str:
        f = self.omnetpp_root / "Version"
        return f.read_text().strip() if f.is_file() else "unknown"

    @property
    def inet_version(self) -> str:
        f = self.inet_root / "Version"
        return f.read_text().strip() if f.is_file() else self.inet_root.name

    def inet_library_built(self) -> bool:
        src = self.inet_root / "src"
        return any(
            (src / name).exists()
            for name in ("libINET.dylib", "libINET_dbg.dylib",
                         "libINET.so", "libINET_dbg.so",
                         "INET", "INET_dbg")
        )

    @property
    def showcase_dir(self) -> Path:
        return self.inet_root / "showcases" / "tsn"

    @property
    def default_showcase_ini(self) -> Path:
        return self.showcase_dir / "combiningfeatures" / "invehicle" / "omnetpp.ini"

    # --- shell integration -------------------------------------------------
    def setenv_prefix(self) -> str:
        """A bash snippet that prepares the environment for running INET sims."""
        return (
            f"cd {shlex.quote(str(self.omnetpp_root))}\n"
            f"source ./setenv >/dev/null 2>&1\n"
            f"export INET_ROOT={shlex.quote(str(self.inet_root))}\n"
            f'export PATH="$INET_ROOT/bin:$PATH"'
        )

    def describe(self) -> str:
        built = "built" if self.inet_library_built() else "NOT built"
        return (
            f"OMNeT++ {self.version}  ({self.omnetpp_root})\n"
            f"INET    {self.inet_version} [{built}]  ({self.inet_root})"
        )


def locate_environment(start: Path | None = None) -> OmnetppEnv:
    """Find a usable installation or raise :class:`EnvironmentError`."""
    omnetpp_root = _find_omnetpp_root(start)
    if omnetpp_root is None:
        raise EnvironmentError(
            "Could not locate an OMNeT++ installation. Set OMNETPP_ROOT or run "
            "the tool from within the OMNeT++ directory tree."
        )
    inet_root = _find_inet_root(omnetpp_root)
    if inet_root is None:
        raise EnvironmentError(
            f"Found OMNeT++ at {omnetpp_root} but no INET framework under "
            f"{omnetpp_root / 'samples'}. Set INET_ROOT explicitly."
        )
    return OmnetppEnv(omnetpp_root=omnetpp_root, inet_root=inet_root)
