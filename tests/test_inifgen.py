"""Tests for the ZeroConfigTSN config generator."""

from __future__ import annotations

from tsntool.inifgen import (
    ConfigSpec, TrafficClass, compatibility_warnings, generate_config_text,
    generate_ini_text,
)


def _spec(**kw) -> ConfigSpec:
    base = dict(name="TsnTool_Custom", base="ManualTsn",
                features={}, classes=[])
    base.update(kw)
    return ConfigSpec(**base)


def test_extends_and_header():
    text = generate_config_text(_spec())
    assert "[Config TsnTool_Custom]" in text
    assert "extends = ManualTsn" in text


def test_feature_on_off():
    text = generate_config_text(_spec(features={
        "hasEgressTrafficShaping": "on",
        "hasCutthroughSwitching": "off",
        "hasStreamRedundancy": "inherit",   # inherit emits nothing
    }))
    assert "*.*Switch.hasEgressTrafficShaping = true" in text
    assert "*.*Switch.hasCutthroughSwitching = false" in text
    assert "hasStreamRedundancy" not in text


def test_frame_preemption_adds_mac_phy():
    text = generate_config_text(_spec(features={"hasFramePreemption": "on"}))
    assert "hasFramePreemption = true" in text
    assert 'macLayer.typename = "EthernetPreemptingMacLayer"' in text
    assert 'phyLayer.typename = "EthernetPreemptingPhyLayer"' in text


def test_cbs_and_tas_classes():
    text = generate_config_text(_spec(classes=[
        TrafficClass("A", 6, "CBS", "20Mbps"),
        TrafficClass("B", 4, "TAS", tas_open_us=100, tas_cycle_us=500),
        TrafficClass("C", 0, "off"),
        TrafficClass("D", 1, "inherit"),  # emits nothing
    ]))
    assert 'transmissionSelectionAlgorithm[6].typename = "Ieee8021qCreditBasedShaper"' in text
    assert "transmissionSelectionAlgorithm[6].idleSlope = 20Mbps" in text
    assert "transmissionGate[4].durations = [100us, 400us]" in text  # cycle-open closed
    assert 'transmissionSelectionAlgorithm[4].typename = ""' in text  # TAS clears CBS
    assert 'transmissionSelectionAlgorithm[0].typename = ""' in text  # off
    assert "[1]" not in text  # inherit class not emitted


def test_warnings():
    # per-class CBS but shaping turned off
    w = compatibility_warnings(_spec(
        features={"hasEgressTrafficShaping": "off"},
        classes=[TrafficClass("A", 6, "CBS")]))
    assert any("turned OFF" in x for x in w)
    # preemption warning
    w = compatibility_warnings(_spec(features={"hasFramePreemption": "on"}))
    assert any("preemption" in x.lower() for x in w)
    # no base
    w = compatibility_warnings(_spec(base=""))
    assert any("No base" in x for x in w)


def test_generate_ini_includes_base_file():
    text = generate_ini_text(_spec(), "omnetpp.ini")
    assert "include omnetpp.ini" in text
    assert "[Config TsnTool_Custom]" in text
