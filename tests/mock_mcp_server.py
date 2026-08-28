"""A minimal MCP stdio server used to test McpToolBackend's protocol handling.

Implements initialize / tools/list / tools/call exactly as the MCP spec (and therefore
the real pyjhora-mcp server) speaks them, returning a single fake astrology tool.
"""
from __future__ import annotations

import json
import sys


def reply(frame: dict, result=None, error=None) -> None:
    out = {"jsonrpc": "2.0", "id": frame.get("id")}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        method = frame.get("method")
        if method == "initialize":
            reply(frame, {"protocolVersion": "2024-11-05",
                          "serverInfo": {"name": "mock-pyjhora-mcp", "version": "0.0.1"},
                          "capabilities": {}})
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue  # notifications carry no id; never answered
        elif method == "tools/list":
            reply(frame, {"tools": [{
                "name": "get_rasi_chart",
                "description": "Mock rasi chart",
                "inputSchema": {"type": "object", "properties": {}},
            }]})
        elif method == "tools/call":
            name = frame.get("params", {}).get("name")
            if name == "get_rasi_chart":
                payload = json.dumps({"ascendant": {"rasi": "Capricorn"}})
                reply(frame, {"content": [{"type": "text", "text": payload}],
                              "isError": False})
            else:
                reply(frame, {"content": [{"type": "text",
                                           "text": f"unknown tool {name}"}],
                              "isError": True})
        else:
            reply(frame, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    main()
