# TSN Tool for OMNeT++ / INET

A tool to **configure, run and analyze Time-Sensitive Networking (TSN)
simulations** on OMNeT++ 6.4 + INET 4.6, recreating (and improving on) the
RTaW-Pegase *ZeroConfigTSN* workflow. It is a standalone Python + Qt
application that you can launch from inside the OMNeT++ IDE via *External
Tools* — no Eclipse plugin installation required.

This is the **Step 1** milestone: locate the OMNeT++/INET install, list the
runnable configurations in an `omnetpp.ini`, **run a chosen TSN showcase
configuration** (headless or in Qtenv), optionally expose the built-in **MCP
server**, and show the produced result files. The configuration builder,
results graphs, problem detection and AI analysis arrive in later steps (see
`../TSN_Tool_Development_Proposal.md`).

> Base demo: INET's `showcases/tsn/combiningfeatures/invehicle` — a realistic
> in-vehicle network (6 switches, ~19 end-stations, redundant links, 4 traffic
> classes, multicast video, and a built-in `brokenComponent` fault knob).

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

Pick a configuration, choose **Cmdenv** (headless) or **Qtenv** (GUI), set an
optional run number / sim-time-limit, optionally tick **Enable MCP server**,
and press **Run**. The live simulation log streams into the window and the
**Result files** panel lists the generated `.sca`/`.vec` files. **Query MCP
state** connects to a running simulation's MCP server and prints its state.

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
| `tsntool/runner.py` | launch sims via the `inet` wrapper (`-u Cmdenv -c …`), stream output |
| `tsntool/mcp_client.py` | minimal MCP (Streamable HTTP) client for the built-in server |
| `tsntool/cli.py` | `env` / `list` / `run` / `mcp` / `gui` subcommands |
| `tsntool/gui/app.py` | PySide6 window tying it together |

Simulations are launched through INET's `bin/inet` wrapper (which assembles the
correct `-n`/`-l`/`-x` flags from `$INET_ROOT`), inside a bash invocation that
first sources the OMNeT++ `setenv`.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

## Roadmap

- **Step 1 (this):** run a chosen TSN showcase config from the tool / IDE. ✅
- **Step 2:** *ZeroConfigTSN*-style config builder (feature toggles + per-class
  table + incompatibility warnings) → generate `.ini` → run. *(branch
  `feature/enhanced`)*
- **Step 3:** results tables, per-hop Gantt, histograms, problem detection &
  advice (uses the `brokenComponent` fault scenarios).
- **Step 4:** AI analysis over the MCP server.

See `../TSN_Plugin_Feasibility_and_Plan.md` and
`../TSN_Tool_Development_Proposal.md` for the full plan and cost estimate.
