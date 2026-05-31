"""Command-line interface for tsntool.

    tsntool env                         show detected OMNeT++/INET install
    tsntool list  [INI]                 list runnable configs in an omnetpp.ini
    tsntool run   [INI] -c CONFIG ...   run a config headless (streams output)
    tsntool mcp   --address host:port   query a running simulation's MCP server
    tsntool gui   [INI]                 launch the PySide6 GUI

If INI is omitted, the in-vehicle TSN showcase is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .environment import EnvironmentError, OmnetppEnv, locate_environment
from .inifile import parse_configs, runnable_configs
from .mcp_client import MCPClient, MCPError, result_text
from .runner import RunSpec, SimulationRunner, list_result_files


def _env_or_exit() -> OmnetppEnv:
    try:
        return locate_environment()
    except EnvironmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _resolve_ini(env: OmnetppEnv, ini: str | None) -> Path:
    if ini:
        return Path(ini).resolve()
    return env.default_showcase_ini


# --- subcommands -----------------------------------------------------------
def cmd_env(args) -> int:
    env = _env_or_exit()
    print(env.describe())
    print(f"default showcase: {env.default_showcase_ini}")
    return 0


def cmd_list(args) -> int:
    env = _env_or_exit()
    ini = _resolve_ini(env, args.ini)
    if not ini.is_file():
        print(f"error: no such ini file: {ini}", file=sys.stderr)
        return 2
    print(f"# configs in {ini}\n")
    for c in parse_configs(ini):
        flag = "  " if c.runnable else "* "  # '*' marks abstract/base
        print(f"{flag}{c.name:<28} {c.description}")
    print("\n(* = abstract/base, not directly runnable)")
    return 0


def cmd_run(args) -> int:
    env = _env_or_exit()
    ini = _resolve_ini(env, args.ini)
    if not ini.is_file():
        print(f"error: no such ini file: {ini}", file=sys.stderr)
        return 2
    runnable = {c.name for c in runnable_configs(ini)}
    if args.config not in runnable:
        print(f"warning: '{args.config}' is not a runnable config in {ini.name}; "
              f"available: {', '.join(sorted(runnable))}", file=sys.stderr)
    spec = RunSpec(
        ini_path=ini, config=args.config, run=args.run, ui=args.ui,
        sim_time_limit=args.time_limit,
        mcp_address=args.mcp,
    )
    runner = SimulationRunner(env)
    print(f"$ {runner.preview(spec)}", file=sys.stderr)
    rc = runner.run(spec, on_line=lambda ln: print(ln, flush=True))
    files = list_result_files(spec.sim_dir)
    print(f"\n# exit code {rc}; {len(files)} result file(s) in {spec.sim_dir / 'results'}",
          file=sys.stderr)
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size} bytes)", file=sys.stderr)
    return rc


def cmd_mcp(args) -> int:
    host, _, port = args.address.partition(":")
    client = MCPClient(host=host or "localhost", port=int(port or 8765))
    try:
        info = client.initialize()
        server = (info or {}).get("serverInfo", {})
        print(f"connected to {server.get('name', '?')} {server.get('version', '')}")
        tools = client.list_tools()
        print(f"{len(tools)} tools available")
        if args.tool:
            res = client.call_tool(args.tool, {})
            print(result_text(res) or res)
        else:
            print("\n-- get_simulation_state --")
            print(result_text(client.get_simulation_state()))
    except MCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_gui(args) -> int:
    try:
        from .gui.app import run_gui
    except ImportError as exc:
        print(f"error: GUI needs a Qt binding (pip install 'PySide6'): {exc}", file=sys.stderr)
        return 2
    env = _env_or_exit()
    ini = _resolve_ini(env, args.ini)
    return run_gui(env, ini if ini.is_file() else None)


# --- parser ----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tsntool", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("env", help="show detected OMNeT++/INET installation")
    sp.set_defaults(func=cmd_env)

    sp = sub.add_parser("list", help="list configs in an omnetpp.ini")
    sp.add_argument("ini", nargs="?", help="path to omnetpp.ini (default: in-vehicle showcase)")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("run", help="run a config headless")
    sp.add_argument("ini", nargs="?", help="path to omnetpp.ini (default: in-vehicle showcase)")
    sp.add_argument("-c", "--config", required=True, help="configuration name")
    sp.add_argument("-r", "--run", type=int, help="run number (for iteration sets)")
    sp.add_argument("-u", "--ui", default="Cmdenv", choices=["Cmdenv", "Qtenv"])
    sp.add_argument("--time-limit", help="override sim-time-limit, e.g. 1ms")
    sp.add_argument("--mcp", help="start MCP server at host:port (e.g. localhost:8765)")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("mcp", help="query a running simulation's MCP server")
    sp.add_argument("--address", default="localhost:8765", help="host:port (default localhost:8765)")
    sp.add_argument("--tool", help="call a specific tool with empty args and print the result")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("gui", help="launch the PySide6 GUI")
    sp.add_argument("ini", nargs="?", help="omnetpp.ini to open (default: in-vehicle showcase)")
    sp.set_defaults(func=cmd_gui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
