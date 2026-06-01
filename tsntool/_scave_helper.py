"""Subprocess worker: read an OMNeT++ result file via omnetpp.scave and emit JSON.

Run under a shell that has sourced the OMNeT++ ``setenv`` (so the scave native
bindings find their libraries). :mod:`tsntool.analyzer` invokes this and parses
the JSON, which keeps the heavy/native scave dependency out of the GUI process.

Usage:  python -m tsntool._scave_helper <result.sca>
"""

from __future__ import annotations

import json
import os
import sys


def _to_floats(x):
    try:
        return [float(v) for v in x]
    except (TypeError, ValueError):
        return None


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: _scave_helper <result.sca>"}))
        return 2
    sca = sys.argv[1]

    # Make sure the OMNeT++ python package is importable even if PYTHONPATH
    # wasn't exported (DYLD/lib path still must come from a sourced setenv).
    root = os.environ.get("OMNETPP_ROOT")
    if root:
        sys.path.insert(0, os.path.join(root, "python"))
    try:
        from omnetpp.scave import results
    except Exception as exc:  # pragma: no cover - environment dependent
        print(json.dumps({"error": f"cannot import omnetpp.scave: {exc}"}))
        return 1

    try:
        df = results.read_result_files(sca)
        hist = results.get_histograms(df)
        scalars = results.get_scalars(df)
    except Exception as exc:
        print(json.dumps({"error": f"failed to read {sca}: {exc}"}))
        return 1

    streams = []
    pl = hist[(hist["name"] == "packetLifeTime:histogram")
              & hist["module"].str.contains(r"app\[")]
    for _, r in pl.iterrows():
        item = {
            "module": r["module"],
            "count": int(r["count"]) if r["count"] == r["count"] else 0,
            "mean": float(r["mean"]) if r["mean"] == r["mean"] else 0.0,
            "min": float(r["min"]) if r["min"] == r["min"] else 0.0,
            "max": float(r["max"]) if r["max"] == r["max"] else 0.0,
        }
        for ek, vk in (("binedges", "binvalues"), ("bin_edges", "bin_values")):
            if ek in r and vk in r:
                edges, vals = _to_floats(r[ek]), _to_floats(r[vk])
                if edges and vals:
                    item["binedges"], item["binvalues"] = edges, vals
                break
        streams.append(item)

    jitter = {}
    jit = hist[hist["name"].isin(["packetJitter:histogram", "packetDelayVariation:histogram"])]
    for _, r in jit.iterrows():
        if r["mean"] == r["mean"]:
            jitter[r["module"]] = float(r["mean"])

    drops = []
    for _, r in scalars.iterrows():
        name = str(r["name"])
        val = r["value"]
        if name.endswith(":count") and ("rop" in name) and val == val and val > 0:
            drops.append({"module": r["module"], "name": name, "count": int(val)})

    def total(metric: str) -> int:
        s = scalars[scalars["name"] == metric]["value"].sum()
        return int(s) if s == s else 0

    out = {
        "streams": streams,
        "jitter": jitter,
        "drops": drops,
        "total_sent": total("packetSent:count"),
        "total_received": total("packetReceived:count"),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
