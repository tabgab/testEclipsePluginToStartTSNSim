"""Minimal MCP client for the OMNeT++ built-in simulation MCP server.

The server (``src/envir/mcpserver.cc``) implements the *Streamable HTTP*
transport (MCP spec 2025-03-26) on a single ``/mcp`` endpoint:

  * POST a JSON-RPC 2.0 request, get a plain ``application/json`` response
    (no SSE for request/response).
  * ``initialize`` returns an ``Mcp-Session-Id`` header to echo on later calls.
  * notifications (no ``id``) get HTTP 202.

Start it on any run with ``--mcp-server-address localhost:8765`` (works in both
Cmdenv and Qtenv). This client uses only the standard library so the core tool
has no third-party dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-03-26"


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, host: str = "localhost", port: int = 8765, timeout: float = 15.0):
        self.url = f"http://{host}:{port}/mcp"
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0

    # --- low level ---------------------------------------------------------
    def _post(self, payload: dict, expect_response: bool = True):
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:  # connection refused, timeout, ...
            raise MCPError(f"cannot reach MCP server at {self.url}: {exc}") from exc
        if not expect_response or not body.strip():
            return None
        return json.loads(body)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(self, method: str, params: dict | None = None):
        resp = self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}}
        )
        if isinstance(resp, dict) and resp.get("error"):
            raise MCPError(str(resp["error"]))
        return resp.get("result") if isinstance(resp, dict) else None

    # --- handshake ---------------------------------------------------------
    def initialize(self) -> dict | None:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tsntool", "version": "0.1.0"},
            },
        )
        # required follow-up notification
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        return result

    # --- tools -------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list")
        return (result or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | None:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    # convenience wrappers around common Step-1 tools
    def get_simulation_state(self):
        return self.call_tool("get_simulation_state")

    def get_network_topology(self, max_depth: int = 10):
        return self.call_tool("get_network_topology", {"max_depth": max_depth})

    def list_result_files(self, pattern: str = "*"):
        return self.call_tool("list_result_files", {"pattern": pattern})

    def run_simulation(self, mode: str = "express", time_limit=None, event_limit=None,
                       relative: bool = False, inclusive: bool = True):
        args: dict = {"mode": mode, "relative": relative, "inclusive": inclusive}
        if time_limit is not None:
            args["time_limit"] = time_limit
        if event_limit is not None:
            args["event_limit"] = event_limit
        return self.call_tool("run_simulation", args)

    def stop_simulation(self):
        return self.call_tool("request_stop_simulation")


def result_text(result: dict | None) -> str:
    """Extract the text payload from an MCP tool-call result."""
    if not result:
        return ""
    parts = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)
