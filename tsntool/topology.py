"""Extract a drawable network topology from a NED file.

We parse the network's ``submodules`` (their names and ``@display`` positions)
and ``connections`` directly from the NED source. This gives an accurate,
positioned diagram — faithful to what Qtenv shows — without having to
instantiate the model. Node roles (switch / device / clock) are inferred from
naming and icon hints; link bitrates from the channel type name.

For the running model, the MCP ``get_network_topology`` tool gives the
authoritative instantiated hierarchy (but without coordinates), which later
steps can overlay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .environment import OmnetppEnv


@dataclass
class TopoNode:
    name: str
    x: float = 0.0
    y: float = 0.0
    role: str = "device"      # "switch" | "device" | "clock"
    icon: str = ""

    @property
    def has_position(self) -> bool:
        return not (self.x == 0.0 and self.y == 0.0)


@dataclass
class TopoLink:
    src: str
    dst: str
    channel: str = ""
    bitrate: str = ""


@dataclass
class Topology:
    name: str = ""
    base: str = ""
    nodes: list[TopoNode] = field(default_factory=list)
    links: list[TopoLink] = field(default_factory=list)
    bg_image: Path | None = None
    width: float = 0.0
    height: float = 0.0

    def node(self, name: str) -> TopoNode | None:
        return next((n for n in self.nodes if n.name == name), None)

    def degree(self, name: str) -> int:
        return sum(1 for l in self.links if l.src == name or l.dst == name)

    def summary(self) -> str:
        roles = {}
        for n in self.nodes:
            roles[n.role] = roles.get(n.role, 0) + 1
        parts = ", ".join(f"{v} {k}{'es' if k == 'switch' else 's'}" for k, v in sorted(roles.items()))
        return f"{self.name}: {len(self.nodes)} nodes ({parts}), {len(self.links)} links"


# --- regexes ---------------------------------------------------------------
_NETWORK_RE = re.compile(r"\bnetwork\s+(\w+)\s*(?:extends\s+([\w.]+))?")
_SUBMODULE_RE = re.compile(r"(\w+)\s*:\s*([^{};]+?)\{([^{}]*)\}", re.S)
_CONN_RE = re.compile(
    r"(\w+)\.\w+\+*\s*<-->\s*([\w.]+)?\s*(?:\{[^{}]*\})?\s*<-->\s*(\w+)\.\w+\+*"
)
_DISPLAY_RE = re.compile(r'@display\("([^"]*)"\)')


def _role_for(name: str, icon: str) -> str:
    low = name.lower()
    if low.endswith("switch") or "bridge" in low:
        return "switch"
    if "clock" in low or "gm" == low or icon.endswith("/card"):
        # masterClock / grandmaster clock node
        if "clock" in low:
            return "clock"
    if low.endswith("switch"):
        return "switch"
    return "device"


def _bitrate_for(channel: str) -> str:
    c = channel.lower()
    if "10g" in c:
        return "10 Gbps"
    if "1g" in c:
        return "1 Gbps"
    if "100m" in c:
        return "100 Mbps"
    if "10m" in c:
        return "10 Mbps"
    return channel


def _parse_display(disp: str) -> tuple[float, float, str]:
    x = y = 0.0
    icon = ""
    for part in disp.split(";"):
        part = part.strip()
        if part.startswith("p="):
            coords = part[2:].split(",")
            try:
                x = float(coords[0])
                y = float(coords[1])
            except (ValueError, IndexError):
                pass
        elif part.startswith("i="):
            icon = part[2:].split(",")[0]
    return x, y, icon


def _resolve_bg_image(disp_text: str, env: OmnetppEnv | None) -> tuple[Path | None, float, float]:
    bgi = re.search(r"bgi=([^;\"]+)", disp_text)
    bgb = re.search(r"bgb=([\d.]+),([\d.]+)", disp_text)
    width = float(bgb.group(1)) if bgb else 0.0
    height = float(bgb.group(2)) if bgb else 0.0
    img: Path | None = None
    if bgi and env is not None:
        rel = bgi.group(1).strip()
        roots = [env.inet_root / "images", env.omnetpp_root / "images"]
        for root in roots:
            for ext in (".png", ".jpg", ".gif", ".svg"):
                cand = root / (rel + ext)
                if cand.is_file():
                    img = cand
                    break
            if img:
                break
    return img, width, height


def parse_ned_topology(ned_path: str | Path, env: OmnetppEnv | None = None) -> Topology:
    """Parse the first ``network`` definition in *ned_path* into a Topology."""
    text = Path(ned_path).read_text(encoding="utf-8", errors="replace")
    topo = Topology()

    m = _NETWORK_RE.search(text)
    if m:
        topo.name = m.group(1)
        topo.base = m.group(2) or ""

    # isolate the submodules ... connections ... blocks (best-effort).
    # Start *after* the 'submodules:' label so the keyword is not parsed as a node.
    sub_start = text.find("submodules:")
    conn_start = text.find("connections:")
    sub_text = (
        text[sub_start + len("submodules:"):conn_start]
        if sub_start >= 0 < conn_start else ""
    )
    conn_text = text[conn_start:] if conn_start >= 0 else ""

    reserved = {"types", "channel", "submodules", "connections", "parameters",
                "gates", "network", "module", "import", "package"}
    seen: set[str] = set()
    for sm in _SUBMODULE_RE.finditer(sub_text):
        name, _typespec, body = sm.group(1), sm.group(2), sm.group(3)
        if name in seen or name in reserved:
            continue
        disp = _DISPLAY_RE.search(body)
        x, y, icon = _parse_display(disp.group(1)) if disp else (0.0, 0.0, "")
        topo.nodes.append(TopoNode(name=name, x=x, y=y, role=_role_for(name, icon), icon=icon))
        seen.add(name)

    node_names = {n.name for n in topo.nodes}
    for cm in _CONN_RE.finditer(conn_text):
        src, channel, dst = cm.group(1), cm.group(2), cm.group(3)
        if src in node_names and dst in node_names:
            topo.links.append(
                TopoLink(src=src, dst=dst, channel=channel or "",
                         bitrate=_bitrate_for(channel or ""))
            )

    topo.bg_image, topo.width, topo.height = _resolve_bg_image(text, env)
    if not topo.width and topo.nodes:
        topo.width = max(n.x for n in topo.nodes) + 120
        topo.height = max(n.y for n in topo.nodes) + 120
    return topo


def find_topology(ini_path: str | Path, env: OmnetppEnv | None = None,
                  network: str | None = None) -> Topology | None:
    """Find and parse the NED topology for the network used by *ini_path*.

    Scans ``*.ned`` files next to the ini; prefers an exact network-name match,
    otherwise returns the richest topology found.
    """
    directory = Path(ini_path).parent
    best: Topology | None = None
    for ned in sorted(directory.glob("*.ned")):
        try:
            topo = parse_ned_topology(ned, env)
        except Exception:
            continue
        if not topo.nodes:
            continue
        if network and topo.name == network:
            return topo
        if best is None or len(topo.nodes) > len(best.nodes):
            best = topo
    return best
