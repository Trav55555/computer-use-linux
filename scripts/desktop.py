"""Linux portal session and screenshot-coordinate input; no display-server bypasses."""

from __future__ import annotations

import asyncio
import contextlib
import io
import math
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Any

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from PIL import Image

DEST = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"
SC = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
FRAME_TTL = 60
IDLE_TTL = 300


class DesktopError(RuntimeError):
    """An actionable error that can be shown to the caller."""


def unwrap(value: Any) -> Any:
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrap(item) for item in value]
    return value


@dataclass(frozen=True)
class Frame:
    id: str
    created: float
    width: int
    height: int
    logical_width: int
    logical_height: int
    png: bytes

    def point(self, x: float, y: float) -> tuple[float, float]:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise DesktopError("Coordinates must be finite numbers.")
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise DesktopError(f"Coordinates must be inside {self.width}x{self.height} screenshot.")
        return x * self.logical_width / self.width, y * self.logical_height / self.height


KEYS = {
    "CTRL": 0xFFE3,
    "CONTROL": 0xFFE3,
    "ALT": 0xFFE9,
    "SHIFT": 0xFFE1,
    "SUPER": 0xFFEB,
    "META": 0xFFEB,
    "ENTER": 0xFF0D,
    "RETURN": 0xFF0D,
    "TAB": 0xFF09,
    "ESC": 0xFF1B,
    "ESCAPE": 0xFF1B,
    "BACKSPACE": 0xFF08,
    "DELETE": 0xFFFF,
    "SPACE": 0x20,
    "HOME": 0xFF50,
    "END": 0xFF57,
    "LEFT": 0xFF51,
    "UP": 0xFF52,
    "RIGHT": 0xFF53,
    "DOWN": 0xFF54,
    "PAGEUP": 0xFF55,
    "PAGEDOWN": 0xFF56,
    "INSERT": 0xFF63,
    **{f"F{i}": 0xFFBD + i for i in range(1, 13)},
}
MODIFIERS = {0xFFE3, 0xFFE9, 0xFFE1, 0xFFEB}
BUTTONS = {"left": 272, "right": 273, "middle": 274}


def text_key(char: str) -> int:
    if not char.isascii():
        raise DesktopError(
            "Typing currently supports ASCII only: this portal/keyboard combination can "
            "silently drop Unicode characters. No input was sent."
        )
    if char == "\n":
        return KEYS["ENTER"]
    if char == "\t":
        return KEYS["TAB"]
    code = ord(char)
    if code < 32 or 127 <= code < 160 or 0xD800 <= code <= 0xDFFF:
        raise DesktopError("Text contains an unsupported control character or surrogate.")
    return code


def chord_keys(keys: list[str]) -> list[int]:
    if not 1 <= len(keys) <= 5:
        raise DesktopError("Use one key, or up to four modifiers followed by one key.")
    result = []
    for key in keys:
        if key.upper() in KEYS:
            result.append(KEYS[key.upper()])
        elif len(key) == 1:
            result.append(text_key(key.lower()))
        else:
            raise DesktopError(f"Unknown key: {key}")
    if len(result) != len(set(result)) or any(k not in MODIFIERS for k in result[:-1]):
        raise DesktopError("Put distinct modifiers first and the main key last.")
    return result


class Desktop:
    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.session: str | None = None
        self.node: int | None = None
        self.logical_size: tuple[int, int] | None = None
        self.devices = 0
        self.frame: Frame | None = None
        self.last_used = time.monotonic()
        self.pending: dict[str, asyncio.Future] = {}
        self.revoked = False
        self.lock = asyncio.Lock()
        self.portal_owner: str | None = None

    async def call(
        self,
        interface: str,
        member: str,
        signature: str = "",
        body: list | None = None,
        path: str = PATH,
    ) -> Message:
        if self.bus is None:
            raise DesktopError("Desktop session is disconnected. Call computer_start.")
        reply = await asyncio.wait_for(
            self.bus.call(
                Message(
                    destination=DEST,
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            ),
            10,
        )
        if reply is None or reply.message_type == MessageType.ERROR:
            error = f"{reply.error_name}: {reply.body}" if reply else "no reply"
            raise DesktopError(f"Portal {member} failed: {error}")
        return reply

    def receive(self, msg: Message) -> None:
        if msg.message_type != MessageType.SIGNAL or msg.sender != self.portal_owner:
            return
        if msg.interface == REQUEST and msg.member == "Response":
            future = self.pending.get(msg.path or "")
            if future is not None and not future.done():
                future.set_result(msg.body)
        elif msg.interface == SESSION and msg.member == "Closed" and msg.path == self.session:
            self.revoked = True
            self.frame = None

    async def request(
        self,
        interface: str,
        member: str,
        signature: str,
        body: list,
        options: dict[str, Variant],
    ) -> dict:
        assert self.bus is not None and self.bus.unique_name is not None
        token = "linux_cua_" + uuid.uuid4().hex
        handle = f"{PATH}/request/{self.bus.unique_name[1:].replace('.', '_')}/{token}"
        future = asyncio.get_running_loop().create_future()
        self.pending[handle] = future
        try:
            reply = await self.call(
                interface,
                member,
                signature,
                body + [{**options, "handle_token": Variant("s", token)}],
            )
            if reply.body[0] != handle:
                raise DesktopError("Portal returned an unexpected request handle.")
            code, results = await asyncio.wait_for(future, 120)
            if code:
                raise DesktopError(f"{member}: desktop permission cancelled or denied ({code}).")
            return unwrap(results)
        except BaseException:
            with contextlib.suppress(Exception):
                await self.call(REQUEST, "Close", path=handle)
            raise
        finally:
            self.pending.pop(handle, None)

    async def start(self) -> dict:
        if self.status()["active"]:
            return self.status()
        await self.close()
        for binary in ("gst-launch-1.0", "gst-inspect-1.0"):
            if not shutil.which(binary):
                raise DesktopError(f"Install {binary}: see README.md desktop dependencies.")
        # Check all plugins before opening a permission dialog.
        for plugin in ("pipewiresrc", "videoconvert", "pngenc", "fdsink"):
            proc = await asyncio.create_subprocess_exec(
                "gst-inspect-1.0",
                plugin,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, error = await asyncio.wait_for(proc.communicate(), 10)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if proc.returncode:
                raise DesktopError(f"Missing GStreamer plugin {plugin}: {error.decode()}")
        try:
            self.bus = await MessageBus(negotiate_unix_fd=True).connect()
            reply = await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="GetNameOwner",
                    signature="s",
                    body=[DEST],
                )
            )
            if reply is None or reply.message_type == MessageType.ERROR:
                raise DesktopError("The desktop portal is not running in this login session.")
            self.portal_owner = reply.body[0]
            self.bus.add_message_handler(self.receive)
            for interface in (REQUEST, SESSION):
                reply = await self.bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="AddMatch",
                        signature="s",
                        body=[f"type='signal',sender='{DEST}',interface='{interface}'"],
                    )
                )
                if reply is None or reply.message_type == MessageType.ERROR:
                    raise DesktopError("Could not subscribe to portal session events.")
            result = await self.request(
                RD,
                "CreateSession",
                "a{sv}",
                [],
                {
                    "session_handle_token": Variant("s", "linux_cua_" + uuid.uuid4().hex),
                },
            )
            self.session = result["session_handle"]
            self.revoked = False
            await self.request(
                RD,
                "SelectDevices",
                "oa{sv}",
                [self.session],
                {
                    "types": Variant("u", 3),
                    "persist_mode": Variant("u", 0),
                },
            )
            await self.request(
                SC,
                "SelectSources",
                "oa{sv}",
                [self.session],
                {
                    "types": Variant("u", 1),
                    "multiple": Variant("b", False),
                    "cursor_mode": Variant("u", 2),
                },
            )
            result = await self.request(RD, "Start", "osa{sv}", [self.session, ""], {})
            streams = result.get("streams", [])
            if len(streams) != 1 or result.get("devices", 0) & 3 != 3:
                raise DesktopError(
                    "Select one monitor and enable Allow Remote Interaction in GNOME's "
                    "sharing dialog to grant keyboard and pointer access. "
                    f"Portal returned {len(streams)} streams and device mask "
                    f"{result.get('devices', 0)} (required: 3)."
                )
            self.node, props = streams[0]
            # Older portals call the logical coordinate dimensions 'size'.
            size = props.get("logical_size", props.get("size"))
            if not size or len(size) != 2 or any(int(n) <= 0 for n in size):
                raise DesktopError("Portal omitted logical monitor size; cannot map clicks safely.")
            self.logical_size = (int(size[0]), int(size[1]))
            self.devices = result["devices"]
            self.last_used = time.monotonic()
            return self.status()
        except BaseException:
            await self.close()
            raise

    def status(self) -> dict:
        active = bool(
            self.session
            and not self.revoked
            and self.bus
            and self.bus.connected
            and time.monotonic() - self.last_used <= IDLE_TTL
        )
        return {
            "active": active,
            "backend": "xdg-desktop-portal",
            "logical_size": self.logical_size if active else None,
            "idle_timeout_seconds": IDLE_TTL,
            "scope": "Selected monitor capture; foreground input can affect the whole desktop.",
            "latest_frame_id": self.frame.id if self.frame and active else None,
        }

    def ensure_active(self) -> None:
        if not self.status()["active"] or time.monotonic() - self.last_used > IDLE_TTL:
            self.frame = None
            raise DesktopError("Desktop session ended or expired. Call computer_start again.")
        self.last_used = time.monotonic()

    def require_frame(self, frame_id: str) -> Frame:
        self.ensure_active()
        frame = self.frame
        if frame is None or frame.id != frame_id or time.monotonic() - frame.created > FRAME_TTL:
            raise DesktopError("Screenshot is missing, stale, or already used. Take a screenshot.")
        return frame

    async def screenshot(self) -> Frame:
        self.ensure_active()
        self.frame = None
        reply = await self.call(SC, "OpenPipeWireRemote", "oa{sv}", [self.session, {}])
        if not reply.unix_fds or not 0 <= reply.body[0] < len(reply.unix_fds):
            raise DesktopError("Portal did not return a PipeWire file descriptor.")
        fd = reply.unix_fds[reply.body[0]]
        try:
            proc = await asyncio.create_subprocess_exec(
                "gst-launch-1.0",
                "-q",
                "pipewiresrc",
                f"fd={fd}",
                f"path={self.node}",
                "num-buffers=1",
                "!",
                "videoconvert",
                "!",
                "pngenc",
                "!",
                "fdsink",
                "fd=1",
                pass_fds=(fd,),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                png, error = await asyncio.wait_for(proc.communicate(), 15)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if proc.returncode or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise DesktopError(f"Screen capture failed ({proc.returncode}): {error.decode()}")
        finally:
            for received_fd in reply.unix_fds:
                os.close(received_fd)
        # Capture may have completed while the user revoked the session.
        self.ensure_active()
        assert self.logical_size is not None
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            image.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
            self.frame = Frame(
                uuid.uuid4().hex,
                time.monotonic(),
                image.width,
                image.height,
                *self.logical_size,
                output.getvalue(),
            )
        return self.frame

    async def notify(self, member: str, signature: str, *args: Any) -> None:
        self.ensure_active()
        await self.call(RD, member, "oa{sv}" + signature, [self.session, {}, *args])

    async def move(self, point: tuple[float, float]) -> None:
        await self.notify("NotifyPointerMotionAbsolute", "udd", self.node, *point)

    async def release(self, member: str, code: int) -> None:
        # Also attempt release after an action error. A closed portal session ends input ownership.
        if self.session and self.bus and self.bus.connected and not self.revoked:
            await self.call(RD, member, "oa{sv}iu", [self.session, {}, code, 0])

    async def click(self, frame_id: str, x: float, y: float, button: str, count: int) -> None:
        point = self.require_frame(frame_id).point(x, y)
        if button not in BUTTONS or count not in (1, 2):
            raise DesktopError("Use left/right/middle and a click count of 1 or 2.")
        self.frame = None
        await self.move(point)
        for _ in range(count):
            try:
                await self.notify("NotifyPointerButton", "iu", BUTTONS[button], 1)
                await asyncio.sleep(0.04)
            finally:
                await self.release("NotifyPointerButton", BUTTONS[button])
            await asyncio.sleep(0.06)

    async def type_text(self, frame_id: str, text: str) -> None:
        self.require_frame(frame_id)
        if not 1 <= len(text) <= 1000:
            raise DesktopError("Type between 1 and 1000 characters per call.")
        symbols = [text_key(char) for char in text]
        self.frame = None
        for symbol in symbols:
            try:
                await self.notify("NotifyKeyboardKeysym", "iu", symbol, 1)
            finally:
                await self.release("NotifyKeyboardKeysym", symbol)

    async def key(self, frame_id: str, keys: list[str]) -> None:
        self.require_frame(frame_id)
        symbols = chord_keys(keys)
        self.frame = None
        pressed = []
        try:
            for symbol in symbols:
                pressed.append(symbol)
                await self.notify("NotifyKeyboardKeysym", "iu", symbol, 1)
        finally:
            # Try each release even if an earlier one fails, then close on any failure.
            failed = False
            for symbol in reversed(pressed):
                try:
                    await self.release("NotifyKeyboardKeysym", symbol)
                except Exception:
                    failed = True
            if failed:
                await self.close()
                raise DesktopError("Could not release all keys; desktop session was closed.")

    async def scroll(self, frame_id: str, x: float, y: float, direction: str, steps: int) -> None:
        point = self.require_frame(frame_id).point(x, y)
        if direction not in ("up", "down", "left", "right") or not 1 <= steps <= 20:
            raise DesktopError("Use up/down/left/right and between 1 and 20 scroll steps.")
        self.frame = None
        await self.move(point)
        axis = 0 if direction in ("up", "down") else 1
        signed = -steps if direction in ("up", "left") else steps
        await self.notify("NotifyPointerAxisDiscrete", "ui", axis, signed)

    async def drag(self, frame_id: str, x: float, y: float, end_x: float, end_y: float) -> None:
        frame = self.require_frame(frame_id)
        start, end = frame.point(x, y), frame.point(end_x, end_y)
        self.frame = None
        await self.move(start)
        try:
            await self.notify("NotifyPointerButton", "iu", BUTTONS["left"], 1)
            for step in range(1, 21):
                await self.move(
                    (
                        start[0] + (end[0] - start[0]) * step / 20,
                        start[1] + (end[1] - start[1]) * step / 20,
                    )
                )
                await asyncio.sleep(0.02)
        finally:
            await self.release("NotifyPointerButton", BUTTONS["left"])

    async def close(self) -> None:
        try:
            if self.session and self.bus and self.bus.connected:
                with contextlib.suppress(Exception):
                    await self.call(SESSION, "Close", path=self.session)
        finally:
            if self.bus:
                self.bus.disconnect()
            self.bus = None
            self.session = None
            self.node = None
            self.frame = None
            self.logical_size = None
            self.devices = 0
            self.revoked = False

    async def expire_idle(self) -> None:
        while True:
            await asyncio.sleep(5)
            async with self.lock:
                if self.session and (self.revoked or time.monotonic() - self.last_used > IDLE_TTL):
                    await self.close()
