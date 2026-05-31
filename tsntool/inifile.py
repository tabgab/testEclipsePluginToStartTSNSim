"""Light parser for OMNeT++ ``omnetpp.ini`` files.

``omnetpp.ini`` is *not* a standard INI file (keys contain ``*``, ``[``, ``]``,
section names are ``[Config Name]``), so we avoid :mod:`configparser` and scan
for what we need: the configuration sections, their ``description``,
``extends`` and ``abstract`` flags. This is enough to populate the
configuration picker. The authoritative run list (with run counts) can later be
obtained from the simulation binary via ``inet -a``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTION_RE = re.compile(r"^\[\s*(General|Config\s+([A-Za-z0-9_]+))\s*\]\s*$")


@dataclass
class IniConfig:
    name: str
    description: str = ""
    extends: str = ""
    abstract: bool = False
    network: str = ""
    line: int = 0

    @property
    def runnable(self) -> bool:
        # 'General' and configs explicitly marked abstract are base configs and
        # cannot be run directly.
        return not self.abstract and self.name != "General"

    @property
    def label(self) -> str:
        return self.name if not self.description else f"{self.name} — {self.description}"


def _strip_value(raw: str) -> str:
    val = raw.split("=", 1)[1].strip() if "=" in raw else ""
    # drop inline comment (best-effort; values rarely contain '#')
    if "#" in val and not val.lstrip().startswith('"'):
        val = val.split("#", 1)[0].strip()
    return val.strip().strip('"')


def parse_configs(ini_path: str | Path) -> list[IniConfig]:
    """Return the configuration sections found in *ini_path*, in file order."""
    path = Path(ini_path)
    configs: list[IniConfig] = []
    current: IniConfig | None = None
    with path.open(encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            m = _SECTION_RE.match(line.strip())
            if m:
                name = "General" if m.group(1) == "General" else m.group(2)
                current = IniConfig(name=name, line=lineno)
                configs.append(current)
                continue
            if current is None:
                continue
            s = line.strip()
            if not s or s.startswith(("#", ";")):
                continue
            key = s.split("=", 1)[0].strip()
            if key == "description":
                current.description = _strip_value(s)
            elif key == "extends":
                current.extends = _strip_value(s)
            elif key == "abstract":
                current.abstract = _strip_value(s).lower() in ("true", "1", "yes")
            elif key == "network":
                current.network = _strip_value(s)
    return configs


def runnable_configs(ini_path: str | Path) -> list[IniConfig]:
    return [c for c in parse_configs(ini_path) if c.runnable]
