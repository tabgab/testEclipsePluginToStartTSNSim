"""Tests for the omnetpp.ini config parser and (if available) the real showcase."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tsntool.inifile import parse_configs, runnable_configs

SAMPLE = textwrap.dedent(
    """
    [General]
    network = Demo
    abstract = true

    [Config StandardEthernet]
    description = "Using only standard Ethernet features"
    *.foo.bar = 1

    [Config TsnBase]
    abstract = true
    description = "base"

    [Config AutomaticTsn]
    extends = TsnBase
    description = "Automatic TSN configuration"
    """
)


def test_parse_basic(tmp_path: Path):
    ini = tmp_path / "omnetpp.ini"
    ini.write_text(SAMPLE)
    configs = {c.name: c for c in parse_configs(ini)}
    assert set(configs) == {"General", "StandardEthernet", "TsnBase", "AutomaticTsn"}
    assert configs["StandardEthernet"].description == "Using only standard Ethernet features"
    assert configs["AutomaticTsn"].extends == "TsnBase"
    assert configs["TsnBase"].abstract is True
    assert configs["General"].abstract is True


def test_runnable_excludes_abstract_and_general(tmp_path: Path):
    ini = tmp_path / "omnetpp.ini"
    ini.write_text(SAMPLE)
    names = {c.name for c in runnable_configs(ini)}
    assert names == {"StandardEthernet", "AutomaticTsn"}


def test_real_invehicle_showcase_if_present():
    """If a real OMNeT++/INET install is reachable, parse the in-vehicle ini."""
    try:
        from tsntool.environment import locate_environment
        env = locate_environment()
    except Exception:
        pytest.skip("no OMNeT++/INET installation detected")
    ini = env.default_showcase_ini
    if not ini.is_file():
        pytest.skip(f"in-vehicle showcase not found at {ini}")
    runnable = {c.name for c in runnable_configs(ini)}
    # these are the runnable configs verified in the showcase
    assert {"StandardEthernet", "ManualTsn", "AutomaticTsn"} <= runnable
    assert "TimeSensitiveNetworkingBase" not in runnable  # abstract
