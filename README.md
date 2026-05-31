# TSN Tool for OMNeT++ / INET

A tool to **configure, run and analyze Time-Sensitive Networking (TSN)
simulations** on OMNeT++ 6.4 + INET 4.6, recreating (and improving on) the
RTaW-Pegase *ZeroConfigTSN* workflow. It is a standalone Python + Qt
application that you can launch from inside the OMNeT++ IDE via *External
Tools* — no Eclipse plugin installation required.

**Step 1** (done): locate the OMNeT++/INET install, list runnable configs, run
a chosen TSN showcase configuration (headless or Qtenv), optionally expose the
built-in **MCP server**, and show the produced result files.

**Step 2** (branch `feature/enhanced`): a **topology view** (the network diagram
drawn from the NED, color-coded by role, links styled by bitrate), a **live run
monitor** (progress, sim time, events, speed, memory), and a *ZeroConfigTSN*-style
**configuration builder** (TSN feature toggles + per-traffic-class shaping +
incompatibility warnings → generate a runnable config → run).

> Base demo: INET's `showcases/tsn/combiningfeatures/invehicle` — a realistic
> in-vehicle network (6 switches, ~19 end-stations, redundant links, 4 traffic
> classes, multicast video, and a built-in `brokenComponent` fault knob).

![In-vehicle topology](docs/invehicle-topology.png)

## Requirements

- OMNeT++ 6.4.0 (this build) with INET 4.6.0 built (`libINET_dbg.dylib`).
- Python 3.13 or 3.14. PySide6 is needed only for the GUI; the CLI is pure
  stdlib.

## Install

```bash
cd tsn-tool
./setup_env.sh           # creates .venv, installs the tool + PySide6
source .venv/bin/activate
```

`setup_env.sh` tries the system Python first and automatically falls back to
Python 3.13 if PySide6 has no wheel for the current interpreter.

## Usage (CLI)

```bash
tsntool env                       # show the detected OMNeT++/INET installation
tsntool list                      # list configs in the in-vehicle showcase
tsntool list path/to/omnetpp.ini  # ... or in any ini

# show / render the network topology
tsntool topology                            # text summary of nodes + links
tsntool topology --render topology.png      # render the diagram to a PNG

# run a config headless (streams the simulation log; writes results/)
tsntool run -c StandardEthernet --time-limit 1ms
tsntool run -c AutomaticTsn

# run with the MCP server exposed, then query it from another shell
tsntool run -c AutomaticTsn --mcp localhost:8765
tsntool mcp --address localhost:8765
```

> **MCP note:** when the MCP server is enabled, a Cmdenv run prints
> *"Waiting for MCP requests…"* and **waits to be driven** (via the
> `run_simulation` tool) instead of running to completion. This is the control
> model later steps build on. Use `tsntool mcp` to inspect it, or run in Qtenv.

If you omit the ini path, the in-vehicle TSN showcase is used.

## Usage (GUI)

```bash
tsntool gui                       # opens on the in-vehicle showcase
tsntool gui path/to/omnetpp.ini
```

The GUI has two tabs:

- **Run && Monitor** — pick a configuration, choose **Cmdenv** (headless) or
  **Qtenv** (GUI), set an optional run number / sim-time-limit, optionally tick
  **Enable MCP server**, and press **Run**. A **live monitor** shows a progress
  bar plus sim time, event count, ev/sec, simsec/sec, message counts and memory
  (parsed from Cmdenv's status output); the raw log streams below, and the
  **Result files** panel lists the generated `.sca`/`.vec`. **Query MCP state**
  connects to a running simulation's MCP server.
- **Configure (ZeroConfigTSN)** — build a runnable config without hand-editing
  ini. Pick a **base** to extend (e.g. `ManualTsn`), set **TSN feature toggles**
  (egress shaping, ingress filtering/PSFP, frame preemption, gPTP, FRER,
  cut-through — each *inherit/on/off*), and edit a **per-traffic-class table**
  (index, scheduling FIFO/CBS/TAS, CBS idleSlope, TAS gate timings). A live panel
  shows **compatibility warnings**; the generated config is previewed and, on
  **Generate, save & run**, written to a separate `tsntool_generated.ini` (the
  showcase is never modified) and executed.

  ![Config builder](docs/config-builder.png)
- **Topology** — the network diagram parsed from the NED `@display` positions,
  with the background image (e.g. the car) behind it. Switches/devices/clock are
  color-coded and links styled by bitrate. Click a node to see its role, port
  count and neighbours; scroll to zoom, drag to pan.

## Launch from the OMNeT++ IDE

1. *Run ▸ External Tools ▸ External Tools Configurations…*
2. Either import [`ide/TSN-Tool.launch`](ide/TSN-Tool.launch) or create a new
   *Program* configuration:
   - **Location:** `…/tsn-tool/run_tsntool.sh`
   - **Arguments:** `gui ${resource_loc}`
   - **Working directory:** `…/tsn-tool`
3. Select an `omnetpp.ini` in the Project Explorer and run the configuration —
   the TSN Tool opens on that ini.

(The `.launch` file contains an absolute path; adjust it if you move the tool.)

## How it works

| Module | Role |
|---|---|
| `tsntool/environment.py` | locate OMNeT++/INET; build the `setenv` + `INET_ROOT` shell prefix |
| `tsntool/inifile.py` | parse `omnetpp.ini` for runnable `[Config …]` sections |
| `tsntool/topology.py` | parse a NED network into nodes/links/positions for the diagram |
| `tsntool/inifgen.py` | generate a runnable `[Config]` (extend a base + feature/shaper overrides) + compatibility checks |
| `tsntool/runner.py` | launch sims via the `inet` wrapper (`-u Cmdenv -c …`), stream output |
| `tsntool/mcp_client.py` | minimal MCP (Streamable HTTP) client for the built-in server |
| `tsntool/cli.py` | `env` / `list` / `topology` / `run` / `mcp` / `gui` subcommands |
| `tsntool/gui/app.py` | PySide6 window (Run & Monitor + Configure + Topology tabs) |
| `tsntool/gui/topology_view.py` | QGraphicsView topology diagram + PNG renderer |

Simulations are launched through INET's `bin/inet` wrapper (which assembles the
correct `-n`/`-l`/`-x` flags from `$INET_ROOT`), inside a bash invocation that
first sources the OMNeT++ `setenv`.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

## Roadmap

- **Step 1:** run a chosen TSN showcase config from the tool / IDE. ✅
- **Step 2 (`feature/enhanced`):** topology view ✅ + live run monitor ✅ +
  *ZeroConfigTSN*-style config builder ✅ (feature toggles + per-class table +
  incompatibility warnings → generate `.ini` → run).
- **Step 3 (next):** results tables, per-hop Gantt, histograms, problem
  detection & advice (using the `brokenComponent` fault scenarios).
- **Step 3:** results tables, per-hop Gantt, histograms, problem detection &
  advice (uses the `brokenComponent` fault scenarios).
- **Step 4:** AI analysis over the MCP server.

See `../TSN_Plugin_Feasibility_and_Plan.md` and
`../TSN_Tool_Development_Proposal.md` for the full plan and cost estimate.
