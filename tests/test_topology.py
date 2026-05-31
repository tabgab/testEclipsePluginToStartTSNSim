"""Tests for the NED topology parser."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tsntool.topology import parse_ned_topology

NED = textwrap.dedent(
    """
    network MiniNet
    {
        parameters:
            @display("bgi=background/car;bgb=1280,720");
        submodules:
            coreSwitch: <> like IEthernetNetworkNode { @display("p=100,100"); }
            host1: <> like IEthernetNetworkNode { @display("p=200,100"); }
            masterClock: <> like IEthernetNetworkNode { @display("p=150,200;i=device/card"); }
        connections:
            coreSwitch.ethg++ <--> Eth1G <--> host1.ethg++;
            masterClock.ethg++ <--> Eth100M <--> coreSwitch.ethg++;
    }
    """
)


def test_parse_minimal(tmp_path: Path):
    ned = tmp_path / "MiniNet.ned"
    ned.write_text(NED)
    topo = parse_ned_topology(ned)
    assert topo.name == "MiniNet"
    names = {n.name for n in topo.nodes}
    assert names == {"coreSwitch", "host1", "masterClock"}
    assert topo.node("coreSwitch").role == "switch"
    assert topo.node("masterClock").role == "clock"
    assert topo.node("host1").role == "device"
    assert topo.node("host1").x == 200 and topo.node("host1").y == 100
    assert len(topo.links) == 2
    assert topo.degree("coreSwitch") == 2
    # bitrate from channel name
    rates = {l.bitrate for l in topo.links}
    assert "1 Gbps" in rates and "100 Mbps" in rates


def test_keyword_not_parsed_as_node(tmp_path: Path):
    """Regression: the 'submodules:' label must not be captured as a node."""
    ned = tmp_path / "MiniNet.ned"
    ned.write_text(NED)
    topo = parse_ned_topology(ned)
    assert topo.node("submodules") is None
    assert topo.node("coreSwitch") is not None  # first real node not swallowed


def test_real_invehicle_topology_if_present():
    try:
        from tsntool.environment import locate_environment
        env = locate_environment()
    except Exception:
        pytest.skip("no OMNeT++/INET installation detected")
    ned = env.default_showcase_ini.parent / "InVehicleNetworkShowcase.ned"
    if not ned.is_file():
        pytest.skip("in-vehicle showcase NED not found")
    topo = parse_ned_topology(ned, env)
    assert len(topo.nodes) == 21
    assert sum(1 for n in topo.nodes if n.role == "switch") == 6
    assert topo.degree("frontSwitch") == 9
    assert topo.bg_image is not None and topo.bg_image.name == "car.png"
