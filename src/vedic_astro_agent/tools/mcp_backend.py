"""MCP backend: route every tool call through a pyjhora-mcp server.

Targets https://github.com/chinmay-sh/pyjhora-mcp (FastMCP). Two transports are
supported, both implemented here with the standard library only:

* **stdio** — spawn the server process and speak JSON-RPC over stdin/stdout
  (what Claude Desktop and ``fastmcp run src/pyjhora_mcp/server.py`` use).
* **HTTP**  — POST JSON-RPC to the server's ``/mcp`` endpoint; both plain JSON and
  SSE-wrapped responses are parsed (FastMCP replies ``text/event-stream``).

The backend adopts the server's tool catalog as its own, so the agent's planner sees
exactly the tools that remote server exposes.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import urllib.request
from typing import Any

from vedic_astro_agent.tools.registry import ToolBackend, ToolCallError, ToolCatalog, ToolSpec

_PROTOCOL_VERSION = "2024-11-05"


class _StdioSession:
    """Minimal MCP JSON-RPC client over a child process's stdio."""

    def __init__(self, command: str, env: dict[str, str] | None = None):
        self._proc = subprocess.Popen(
            shlex.split(command),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env={**os.environ, **(env or {})},
        )
        self._id = 0
        self._lock = threading.Lock()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            self._id += 1
            message = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                message["params"] = params
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise ToolCallError("pyjhora-mcp server closed the connection")
                line = line.strip()
                if not line:
                    continue
                try:
                    reply = json.loads(line)
                except json.JSONDecodeError:
                    continue  # ignore non-JSON chatter on stdout
                if reply.get("id") != self._id:
                    continue  # notifications / mismatched frames
                if "error" in reply:
                    raise ToolCallError(
                        f"MCP error on {method}: {reply['error'].get('message', reply['error'])}"
                    )
                return reply.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification — no id, and the server never replies to one."""
        with self._lock:
            message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            assert self._proc.stdin
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self.notify("notifications/cancelled")
        except Exception:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class _HttpSession:
    """Minimal MCP JSON-RPC client over HTTP(S) — handles JSON and SSE responses."""

    def __init__(self, url: str, api_key: str | None = None):
        self._url = url
        self._id = 0
        self._lock = threading.Lock()
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            self._id += 1
            payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                payload["params"] = params
            req = urllib.request.Request(
                self._url, data=json.dumps(payload).encode(), headers=self._headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode()
                content_type = resp.headers.get("Content-Type", "")
            reply = self._parse(body, content_type)
            if "error" in reply:
                raise ToolCallError(
                    f"MCP error on {method}: {reply['error'].get('message', reply['error'])}"
                )
            return reply.get("result")

    @staticmethod
    def _parse(body: str, content_type: str) -> dict[str, Any]:
        if "text/event-stream" in content_type:
            # SSE frame: keep the last 'data:' payload carrying the JSON-RPC reply.
            data_lines = [ln[5:].strip() for ln in body.splitlines()
                          if ln.startswith("data:")]
            if not data_lines:
                raise ToolCallError(f"empty SSE response: {body[:200]!r}")
            return json.loads(data_lines[-1])
        return json.loads(body)

    def close(self) -> None:
        pass


class McpToolBackend(ToolBackend):
    """A ToolBackend delegating to a running pyjhora-mcp server."""

    name = "mcp"

    def __init__(self, command: str | None = None, url: str | None = None,
                 api_key: str | None = None, env: dict[str, str] | None = None):
        if command:
            self._session = _StdioSession(command, env=env)
            self._init(stdio=True)
        elif url:
            self._session = _HttpSession(url, api_key)
            self._init(stdio=False)
        else:
            raise ToolCallError(
                "McpToolBackend needs command= (stdio) or url= (http); see .env.example "
                "(VEDIC_MCP_COMMAND / VEDIC_MCP_URL)."
            )
        self._catalog: ToolCatalog | None = None

    def _init(self, stdio: bool) -> None:
        result = self._session.request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "vedic-astro-agent", "version": "0.1.0"},
        })
        if not result or "serverInfo" not in result:
            raise ToolCallError(f"unexpected MCP initialize response: {result!r}")
        # The initialized notification is required by the spec; servers never reply to it.
        try:
            if stdio:
                self._session.notify("notifications/initialized")
            else:
                self._session.request("notifications/initialized")
        except Exception:
            pass

    def list_tools(self) -> ToolCatalog:
        if self._catalog is None:
            result = self._session.request("tools/list") or {}
            catalog = ToolCatalog()
            for tool in result.get("tools", []):
                def call(_tool_name=tool["name"]):
                    return lambda arguments: self._unwrap(self._session.request(
                        "tools/call", {"name": _tool_name, "arguments": arguments}))

                catalog.add(ToolSpec(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    handler=call(),
                ))
            self._catalog = catalog
        return self._catalog

    @staticmethod
    def _unwrap(result: Any) -> Any:
        """tools/call returns {content: [{type:'text', text: <json>}], isError?}."""
        if result is None:
            raise ToolCallError("empty MCP tools/call result")
        if result.get("isError"):
            text = "".join(c.get("text", "") for c in result.get("content", []))
            raise ToolCallError(f"tool failed: {text[:500]}")
        texts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        if not texts:
            return {}
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"text": "\n".join(texts)}

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        spec = self.list_tools().get(tool_name)
        return spec.handler(arguments)

    def close(self) -> None:
        self._session.close()
