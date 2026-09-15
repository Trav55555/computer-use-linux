import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest
from dbus_next import Message, Variant
from dbus_next.aio import MessageBus
from desktop import (
    FRAME_TTL,
    KEYS,
    PATH,
    RD,
    REQUEST,
    SESSION,
    Desktop,
    DesktopError,
    Frame,
    chord_keys,
    text_key,
)


class RecordingDesktop(Desktop):
    def __init__(self):
        super().__init__()
        self.calls = AsyncMock()

    async def call(self, interface, member, signature="", body=None, path=PATH) -> Message:
        return await self.calls(interface, member, signature, body, path=path)


def ready() -> RecordingDesktop:
    desktop = RecordingDesktop()
    desktop.bus = create_autospec(MessageBus, instance=True, connected=True, unique_name=":1.2")
    desktop.session = PATH + "/session/1_2/test"
    desktop.portal_owner = ":1.3"
    desktop.node = 42
    desktop.frame = Frame("frame", time.monotonic(), 1600, 900, 1920, 1080, b"png")
    return desktop


@pytest.mark.parametrize("logical", [(1920, 1080), (2560, 1440), (3840, 2160)])
def test_scaled_coordinates(logical: tuple[int, int]):
    frame = Frame("frame", 0, 1600, 900, *logical, b"")
    assert frame.point(800, 450) == (logical[0] / 2, logical[1] / 2)


@pytest.mark.parametrize(
    "point", [(-1, 1), (1600, 1), (1, 900), (float("nan"), 0), (0, float("inf"))]
)
def test_coordinate_bounds(point):
    frame = ready().frame
    assert frame is not None
    with pytest.raises(DesktopError):
        frame.point(*point)


@pytest.mark.parametrize("mode", ["missing", "wrong", "expired", "revoked"])
async def test_bad_frame_sends_no_input(mode):
    desktop = ready()
    if mode == "missing":
        desktop.frame = None
    elif mode == "expired":
        desktop.frame = Frame("frame", time.monotonic() - FRAME_TTL - 1, 1600, 900, 1920, 1080, b"")
    elif mode == "revoked":
        desktop.revoked = True
    with pytest.raises(DesktopError):
        await desktop.click("wrong" if mode == "wrong" else "frame", 50, 50, "left", 1)
    desktop.calls.assert_not_called()


async def test_click_consumes_frame_and_uses_logical_coordinates():
    desktop = ready()
    await desktop.click("frame", 800, 450, "left", 1)
    calls = desktop.calls.call_args_list
    assert calls[0].args == (
        RD,
        "NotifyPointerMotionAbsolute",
        "oa{sv}udd",
        [desktop.session, {}, 42, 960.0, 540.0],
    )
    assert calls[1].args[3][-2:] == [272, 1]
    assert calls[2].args[3][-2:] == [272, 0]
    with pytest.raises(DesktopError):
        await desktop.click("frame", 800, 450, "left", 1)
    assert len(desktop.calls.call_args_list) == 3


async def test_drag_releases_button_on_failed_motion():
    desktop = ready()
    desktop.calls.side_effect = [None, None, RuntimeError("motion failed"), None]
    with pytest.raises(RuntimeError, match="motion failed"):
        await desktop.drag("frame", 10, 10, 100, 100)
    assert desktop.calls.call_args.args[1] == "NotifyPointerButton"
    assert desktop.calls.call_args.args[3][-2:] == [272, 0]


async def test_key_release_order_on_failed_chord():
    desktop = ready()
    desktop.calls.side_effect = [None, RuntimeError("key failed"), None, None]
    with pytest.raises(RuntimeError, match="key failed"):
        await desktop.key("frame", ["CTRL", "a"])
    assert [call.args[3][-2:] for call in desktop.calls.call_args_list] == [
        [KEYS["CTRL"], 1],
        [ord("a"), 1],
        [ord("a"), 0],
        [KEYS["CTRL"], 0],
    ]


async def test_full_text_validation_before_any_input():
    desktop = ready()
    with pytest.raises(DesktopError):
        await desktop.type_text("frame", "good text\x00bad")
    desktop.calls.assert_not_called()


async def test_unmappable_unicode_is_rejected_before_any_input():
    desktop = ready()
    with pytest.raises(DesktopError, match="ASCII"):
        await desktop.type_text("frame", "café")
    desktop.calls.assert_not_called()


def test_unicode_and_chord_validation():
    for character in ("é", "✓"):
        with pytest.raises(DesktopError, match="ASCII"):
            text_key(character)
    assert chord_keys(["CTRL", "SHIFT", "a"]) == [KEYS["CTRL"], KEYS["SHIFT"], ord("a")]
    for keys in (["a", "b"], ["CTRL", "CTRL", "a"], ["unknown"], []):
        with pytest.raises(DesktopError):
            chord_keys(keys)


async def test_portal_response_arriving_before_method_reply():
    desktop = ready()

    async def call(interface, member, signature="", body=None, path=PATH):
        assert body is not None and desktop.portal_owner is not None
        token = body[-1]["handle_token"].value
        handle = PATH + "/request/1_2/" + token
        msg = Message.new_signal(
            handle, REQUEST, "Response", "ua{sv}", [0, {"session_handle": Variant("s", "ok")}]
        )
        msg.sender = desktop.portal_owner
        desktop.receive(msg)
        return SimpleNamespace(body=[handle])

    desktop.calls.side_effect = call
    assert await desktop.request(RD, "CreateSession", "a{sv}", [], {}) == {"session_handle": "ok"}
    assert desktop.pending == {}


async def test_cancel_closes_pending_portal_request():
    desktop = ready()
    waiting = asyncio.Event()

    async def call(interface, member, signature="", body=None, path=PATH):
        if member == "Close":
            return None
        assert body is not None
        waiting.set()
        token = body[-1]["handle_token"].value
        return SimpleNamespace(body=[PATH + "/request/1_2/" + token])

    desktop.calls.side_effect = call
    task = asyncio.create_task(desktop.request(RD, "Start", "osa{sv}", [desktop.session, ""], {}))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert desktop.calls.call_args.args[:2] == (REQUEST, "Close")
    assert desktop.pending == {}


async def test_revocation_invalidates_frame_immediately():
    desktop = ready()
    assert desktop.session is not None and desktop.portal_owner is not None
    msg = Message.new_signal(desktop.session, SESSION, "Closed", "a{sv}", [{}])
    msg.sender = desktop.portal_owner
    desktop.receive(msg)
    assert desktop.revoked and desktop.frame is None
    with pytest.raises(DesktopError):
        await desktop.key("frame", ["ENTER"])
    desktop.calls.assert_not_called()
