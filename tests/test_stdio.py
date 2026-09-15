"""Use the real MCP subprocess and SDK; no desktop permissions needed."""

import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["computer-use-linux"]


async def test_stdio_handshake_schema_and_unapproved_input():
    params = StdioServerParameters(
        command=CONFIG["command"],
        args=CONFIG["args"],
        cwd=str(ROOT),
        env={key: os.environ[key] for key in CONFIG["env_vars"] if key in os.environ},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        result = await client.initialize()
        assert result.serverInfo.name == "computer-use-linux"
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["computer"]
        assert "drag" in tools.tools[0].inputSchema["properties"]["action"]["enum"]
        status = await client.call_tool("computer", {"action": "status"})
        assert not status.isError
        assert isinstance(status.content[0], TextContent)
        assert json.loads(status.content[0].text)["active"] is False
        denied = await client.call_tool("computer", {"action": "click", "x": 100, "y": 100})
        assert denied.isError
        assert isinstance(denied.content[0], TextContent)
        assert "session ended" in denied.content[0].text.lower()
        invalid = await client.call_tool("computer", {"action": "unknown"})
        assert invalid.isError
        stopped = await client.call_tool("computer", {"action": "stop"})
        assert not stopped.isError
