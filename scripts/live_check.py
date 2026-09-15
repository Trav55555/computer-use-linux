"""Run a harmless GUI round trip through the packaged MCP server after OS consent."""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ImageContent, TextContent
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TEXT = "Linux computer use: verified!"


class TargetState(TypedDict):
    clicks: int
    scrolls: int
    drags: int
    text: str


def read_target(output: Path) -> TargetState:
    clicks, scrolls, drags, text = (output / "target.txt").read_text().split("\n", 3)
    return {
        "clicks": int(clicks),
        "scrolls": int(scrolls),
        "drags": int(drags),
        "text": text.removesuffix("\n"),
    }


def marker_center(png: bytes, color: str) -> tuple[float, float]:
    """Locate a fresh random solid-color target; fail if it is absent or occluded."""
    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color))
        r, g, b = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(r, g), b)
        mask = maximum.point(lambda value: 255 if value < 3 else 0)
        box = mask.getbbox()
        if box is None:
            raise RuntimeError("Test target is not visible on the selected monitor; no input sent.")
        left, top, right, bottom = box
        if right - left < 40 or bottom - top < 15:
            raise RuntimeError("Test target is too small; no input sent.")
        interior = mask.crop((left + 3, top + 3, right - 3, bottom - 3))
        # The embedded mouse cursor can cover a small part of an otherwise clear target.
        if interior.histogram()[255] / (interior.width * interior.height) < 0.97:
            raise RuntimeError("Test target is obscured or ambiguous; no input sent.")
        return (left + right) / 2, (top + bottom) / 2


async def run(output: Path, color: str) -> None:
    config = json.loads(await asyncio.to_thread((ROOT / ".mcp.json").read_text))["mcpServers"][
        "computer-use-linux"
    ]
    params = StdioServerParameters(
        command=config["command"],
        args=config["args"],
        cwd=str(ROOT),
        env={key: os.environ[key] for key in config["env_vars"] if key in os.environ},
    )
    gui = await asyncio.create_subprocess_exec(
        "wish",
        str(ROOT / "scripts/live_target.tcl"),
        str(output),
        color,
    )
    try:
        for _ in range(50):
            if await asyncio.to_thread((output / "target.txt").exists):
                break
            if gui.returncode is not None:
                raise RuntimeError("The test window could not start.")
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("The test window did not become ready.")
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()

            async def action(name: str, **kwargs) -> tuple[dict, bytes]:
                result = await client.call_tool("computer", {"action": name, **kwargs})
                if result.isError:
                    raise RuntimeError(str(result.content))
                metadata = next(item for item in result.content if isinstance(item, TextContent))
                image = next(item for item in result.content if isinstance(item, ImageContent))
                return json.loads(metadata.text), base64.b64decode(image.data)

            try:
                print(
                    "Select the test window's monitor; enable Allow Remote Interaction; Share.",
                    flush=True,
                )
                frame, png = await action("start")
                x, y = await asyncio.to_thread(marker_center, png, color)
                frame, _ = await action("click", frame_id=frame["frame_id"], x=x, y=y)
                frame, _ = await action("type", frame_id=frame["frame_id"], text="temporary")
                frame, _ = await action("key", frame_id=frame["frame_id"], keys=["CTRL", "a"])
                frame, png = await action("type", frame_id=frame["frame_id"], text=EXPECTED_TEXT)
                x, y = await asyncio.to_thread(marker_center, png, color)
                frame, png = await action(
                    "scroll", frame_id=frame["frame_id"], x=x, y=y, direction="down", steps=3
                )
                x, y = await asyncio.to_thread(marker_center, png, color)
                frame, png = await action(
                    "drag", frame_id=frame["frame_id"], x=x - 8, y=y, end_x=x + 8, end_y=y
                )
                state = await asyncio.to_thread(read_target, output)
                if (
                    state["clicks"] != 2
                    or state["text"] != EXPECTED_TEXT
                    or state["scrolls"] < 1
                    or state["drags"] < 1
                ):
                    raise RuntimeError(f"Application did not receive the expected input: {state}")
                await asyncio.to_thread((output / "result.png").write_bytes, png)
                report = {
                    "passed": True,
                    "checks": ["capture", "click", "type", "CTRL+a", "scroll", "drag"],
                    "application_state": state,
                }
                await asyncio.to_thread(
                    (output / "result.json").write_text, json.dumps(report, indent=2)
                )
                print(json.dumps(report), flush=True)
            finally:
                await client.call_tool("computer", {"action": "stop"})
    finally:
        if gui.returncode is None:
            gui.terminate()
        await gui.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--color", default="#" + secrets.token_hex(3), help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="computer-use-linux-check-"))
    output.mkdir(parents=True, exist_ok=True)
    print(f"Evidence directory: {output}", flush=True)
    asyncio.run(run(output, args.color))
