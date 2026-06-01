"""Unit tests for analyzer dataclasses and helpers (no scave needed)."""

from __future__ import annotations

from pathlib import Path

from tsntool.analyzer import (
    AnalysisResult, DropInfo, StreamLatency, _extract_json, find_result_scas,
)


def test_stream_label_and_status():
    s = StreamLatency(module="Net.engineActuator.app[4].sink", count=4,
                      mean_s=120e-6, min_s=100e-6, max_s=126e-6)
    assert s.label == "engineActuator.app[4]"
    assert s.status == "—"            # no deadline
    s.deadline_s = 100e-6
    assert s.status == "FAIL"         # 126us > 100us
    assert round(s.max_us, 1) == 126.0
    s.deadline_s = 200e-6
    assert s.status == "PASS"


def test_drop_reason():
    d = DropInfo("Net.sw.eth[0]", "droppedPacketsQueueOverflow:count", 5)
    assert d.reason == "droppedPacketsQueueOverflow"


def test_extract_json_trailing_line():
    out = "some setenv noise\nwarning: blah\n{\"streams\": [], \"total_sent\": 3}\n"
    data = _extract_json(out)
    assert data == {"streams": [], "total_sent": 3}
    assert _extract_json("no json here") is None


def test_find_result_scas(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    (tmp_path / "omnetpp.ini").write_text("[General]\n")
    for name in ["AutomaticTsn-brokenComponent=none-#0.sca",
                 "AutomaticTsn-brokenComponent=wheel-#0.sca",
                 "StandardEthernet-x-#0.sca"]:
        (results / name).write_text("version 3\n")
    allscas = find_result_scas(tmp_path / "omnetpp.ini")
    assert len(allscas) == 3
    autos = find_result_scas(tmp_path / "omnetpp.ini", config="AutomaticTsn")
    assert len(autos) == 2
    assert all(p.name.startswith("AutomaticTsn-") for p in autos)


def test_analysis_result_totals():
    res = AnalysisResult(sca_path=Path("x.sca"),
                         drops=[DropInfo("m", "a:count", 3), DropInfo("m", "b:count", 4)])
    assert res.ok is True
    assert res.total_drops == 7
