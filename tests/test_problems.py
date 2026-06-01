"""Tests for problem detection (deadline misses + meaningful drops)."""

from __future__ import annotations

from pathlib import Path

from tsntool.analyzer import AnalysisResult, DropInfo, StreamLatency
from tsntool.problems import detect_problems


def _stream(label_mod, maxv, deadline=None):
    s = StreamLatency(module=label_mod, count=4, mean_s=maxv * 0.9, min_s=0.0, max_s=maxv)
    s.deadline_s = deadline
    return s


def test_deadline_miss_flagged():
    res = AnalysisResult(sca_path=Path("x.sca"), streams=[
        _stream("Net.a.app[0].sink", 126e-6, deadline=100e-6),   # miss
        _stream("Net.b.app[0].sink", 50e-6, deadline=100e-6),    # ok
        _stream("Net.c.app[0].sink", 999e-6, deadline=None),     # no deadline -> ignored
    ])
    probs = detect_problems(res)
    titles = [p.title for p in probs]
    assert any("Deadline miss" in t and "a.app[0]" in t for t in titles)
    assert not any("b.app[0]" in t for t in titles)
    assert probs[0].severity == "error"


def test_meaningful_drops_vs_normal_drops():
    res = AnalysisResult(sca_path=Path("x.sca"),
                         drops=[DropInfo("Net.sw1.eth[0]", "droppedPacketsQueueOverflow:count", 12),
                                DropInfo("Net.sw1.eth[1]", "packetDropNotAddressedToUs:count", 999)])
    probs = detect_problems(res)
    titles = " ".join(p.title for p in probs)
    assert "QueueOverflow" in titles           # flagged
    assert "NotAddressedToUs" not in titles     # normal switching drop, ignored


def test_no_problems_gives_info():
    res = AnalysisResult(sca_path=Path("x.sca"),
                         streams=[_stream("Net.a.app[0].sink", 50e-6, deadline=100e-6)])
    probs = detect_problems(res)
    assert len(probs) == 1 and probs[0].severity == "info"


def test_analysis_error_surfaced():
    res = AnalysisResult(sca_path=Path("x.sca"), error="boom")
    probs = detect_problems(res)
    assert probs[0].severity == "error" and "boom" in probs[0].detail
