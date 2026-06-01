"""Turn an :class:`~tsntool.analyzer.AnalysisResult` into actionable findings.

We deliberately do NOT flag every packet drop: in INET many drop reasons are
normal switching behaviour (e.g. *NotAddressedToUs* on flooded ports). Only
congestion/routing/failure-related drops are treated as problems. Deadline
misses (observed max latency above a stream's configured deadline) are the
primary finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from .analyzer import AnalysisResult

# drop-reason substring -> (severity, advice)
PROBLEM_DROP_KINDS: dict[str, tuple[str, str]] = {
    "QueueOverflow": ("error",
        "A queue overflowed — offered traffic exceeds the egress capacity. Increase the "
        "CBS idleSlope for that class, give it a TAS slot, raise the link bitrate, or "
        "reduce the source rate / burst."),
    "NoRouteFound": ("error",
        "No route to destination — a link/path is unavailable. Check the topology and "
        "(for critical streams) that FRER redundancy covers the failed link."),
    "InterfaceDown": ("warning",
        "Traffic hit a down interface (e.g. a broken/disabled link). FRER should reroute "
        "protected streams — verify redundancy covers this path."),
    "HopLimitReached": ("warning",
        "Packets exceeded their hop limit — possible routing loop or too-low hop limit."),
    "LifetimeExpired": ("warning",
        "Packets exceeded their lifetime — excessive buffering/latency upstream."),
    "NoInterfaceFound": ("warning",
        "No outgoing interface found for some packets — check addressing/forwarding tables."),
}


@dataclass
class Problem:
    severity: str  # "error" | "warning" | "info"
    title: str
    detail: str
    advice: str = ""

    @property
    def icon(self) -> str:
        return {"error": "⛔", "warning": "⚠", "info": "ℹ"}.get(self.severity, "•")


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def detect_problems(result: AnalysisResult) -> list[Problem]:
    if not result.ok:
        return [Problem("error", "Could not analyze results", result.error or "unknown error")]

    problems: list[Problem] = []

    # 1) deadline misses
    for s in result.streams:
        if s.deadline_s is not None and s.max_s > s.deadline_s:
            over = (s.max_s - s.deadline_s) * 1e6
            problems.append(Problem(
                "error", f"Deadline miss: {s.label}",
                f"observed max {s.max_us:.1f} µs exceeds the {s.deadline_s * 1e6:.0f} µs "
                f"deadline by {over:.1f} µs (mean {s.mean_us:.1f} µs over {s.count} packets).",
                "Raise this class's priority/idleSlope, give it a dedicated TAS gate slot, "
                "reduce competing load on its path, or relax the deadline if unrealistic."))

    # 2) congestion/routing/failure drops (ignore normal switching drops)
    agg: dict[str, list] = {}
    for d in result.drops:
        for kind, (sev, advice) in PROBLEM_DROP_KINDS.items():
            if kind.lower() in d.name.lower():
                entry = agg.setdefault(kind, [sev, advice, 0, set()])
                entry[2] += d.count
                # short module label
                parts = d.module.split(".")
                entry[3].add(".".join(parts[1:3]) if len(parts) > 2 else d.module)
    for kind, (sev, advice, count, mods) in agg.items():
        shown = ", ".join(sorted(mods)[:4]) + ("…" if len(mods) > 4 else "")
        problems.append(Problem(
            sev, f"{count} packet drop(s): {kind}",
            f"on {len(mods)} module(s): {shown}", advice))

    if not problems:
        problems.append(Problem(
            "info", "No deadline misses or critical drops detected",
            "All analyzed streams met their configured deadlines, and no congestion, "
            "routing or interface-failure drops were recorded."))

    problems.sort(key=lambda p: _SEVERITY_ORDER.get(p.severity, 9))
    return problems
