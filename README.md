---
id: computer-use-linux
tags: [codex, linux, computer-use]
type: project
status: experimental
created: '2026-09-15'
---

# Computer Use Linux

An independent Linux computer-use plugin for Codex. It captures one selected
monitor and sends foreground keyboard and pointer input through XDG Desktop Portal.
It does not contain OpenAI's native Computer Use implementation.

## Use

After installation, start a new Codex thread and ask:

> Use computer-use-linux to test my desktop app.

The `computer` MCP tool provides `start`, `screenshot`, `click`, `type`, `key`,
`scroll`, `drag`, `status`, and `stop`. `start` opens a desktop sharing dialog;
select one monitor and enable **Allow Remote Interaction** before sharing. Screen
sharing alone does not grant keyboard/pointer access. Each input call requires
the latest screenshot's `frame_id`, uses that image's pixel coordinates, and
returns another screenshot. Screenshots expire after 60 seconds; sessions close
after five minutes without tool activity or when Codex disconnects.

Input goes to the foreground desktop. The shared monitor selection limits capture,
but does not isolate input to a particular application. No per-app allow list,
background app control, persistent consent, or automatic OS-dialog approval is
implemented. Avoid using the desktop simultaneously with the agent.

Typing currently supports ASCII text, tabs, and newlines. Non-ASCII input is
rejected before any characters are sent: the tested GNOME/keyboard mapping silently
dropped `é` when injected as a keysym. Unicode typing needs a separately verified
input path; clipboard permissions are not bypassed to implement it.

## Requirements

- A logged-in Linux graphical session with RemoteDesktop and ScreenCast portals.
  Initial target: GNOME on Wayland. Other compositors are unverified.
- `uv`, Python 3.11+, GStreamer, the PipeWire source, PNG encoder, and video converter.
- Runtime dependencies are pinned in `uv.lock`. No OpenAI API key is needed by
  this plugin; Codex supplies the model connection.

Ubuntu/Debian system packages:

```sh
sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome \
  gstreamer1.0-tools gstreamer1.0-pipewire \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

The bundled live test also needs the system `wish` runtime (`sudo apt install tk`).
It deliberately uses the system's matching Tcl/Tk rather than uv Python's bundled
Tkinter, whose Tcl library version may differ. Use a desktop-backed
Codex process: a remote SSH or container process without the login session's
D-Bus access cannot control the desktop. The plugin explicitly forwards the
desktop session variables needed by its MCP subprocess.

## Verification

From this directory:

```sh
uv run ruff check .
uv run ty check .
uv run pytest -q
uv run python3 scripts/live_check.py
```

The final command opens a harmless test window, then requests OS sharing consent.
It identifies a random-color target in the screenshot, clicks it, types text,
uses Ctrl+A, scrolls, and drags. It asserts the application received those events and
saves a screenshot and JSON receipt under `/tmp/computer-use-linux-check-*`.
It stops without sending input if the test target is missing or obscured.

**Current evidence:** 22 automated tests pass, including a real MCP subprocess
handshake and failure-path checks. The live GNOME Wayland test passed screenshot
capture, clicks, ASCII typing, Ctrl+A, scrolling, and dragging. The application
confirmed the exact resulting text and input events. See [VERIFICATION.md](VERIFICATION.md).
Fractional-scale monitors, multi-monitor placement, and other compositors remain
unverified. This is an experimental plugin, not full parity with the native plugin.

## Implementation notes

`scripts/desktop.py` owns the portal session, capture, and input mapping.
`scripts/server.py` exposes it over MCP stdio. Starting the server does not open
the desktop or prompt for access. The server keeps screenshots in memory and
returns them to Codex; only the explicit live test saves evidence images.

No native backend source was found in the public `openai/codex` tree inspected at
commit `a8964cb1bad67bc26a826fb07d1bef99c6a3f008` (2026-09-15). The code here was
written against the public Linux portal APIs. OpenAI documents native Computer Use
as unavailable on Linux at the time of this implementation.

Sources:

- [OpenAI Linux platform limitations](https://learn.chatgpt.com/docs/linux/linux-app#compatibility-and-limitations)
- [RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
- [ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
- [Codex native MCP plugin configuration](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/codex-mcp/src/plugin_config.rs)
