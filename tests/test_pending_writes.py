"""PendingWriteBuffer: staging, confirm, discard, timeout and the v1.4.1 races."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from custom_components.hanchu_ess_ble import pending_writes as pw_module
from custom_components.hanchu_ess_ble.pending_writes import (
    PENDING_TIMEOUT_SECONDS,
    PendingWriteBuffer,
)
from custom_components.hanchu_ess_ble.ble_client import HanchuLinkLostError
from custom_components.hanchu_ess_ble.protocol import HanchuProtocolError


class FakeWriter:
    """Records async_write_values calls; can be paused or made to fail."""

    def __init__(self) -> None:
        self.calls: list[list[tuple[str, object]]] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.error: Exception | None = None

    async def async_write_values(self, pairs):
        self.calls.append(list(pairs))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return []


@pytest.fixture
def timers():
    """Capture async_call_later so tests can fire or inspect the timeout."""
    scheduled: list[dict] = []

    def fake_call_later(_hass, delay, action):
        record = {"delay": delay, "action": action, "cancelled": False}

        def cancel():
            record["cancelled"] = True

        scheduled.append(record)
        return cancel

    with patch.object(pw_module, "async_call_later", side_effect=fake_call_later):
        yield scheduled


@pytest.fixture
def writer() -> FakeWriter:
    return FakeWriter()


@pytest.fixture
def buffer(timers, writer) -> PendingWriteBuffer:
    return PendingWriteBuffer(MagicMock(), writer)


def _active(timers):
    return [t for t in timers if not t["cancelled"]]


def test_stage_marks_pending_and_notifies(buffer, timers):
    listener = MagicMock()
    buffer.add_listener(listener)
    buffer.stage("P100", 5)
    assert buffer.has_pending
    assert buffer.is_pending("P100")
    assert buffer.pending_count == 1
    listener.assert_called_once()
    assert [t["delay"] for t in _active(timers)] == [PENDING_TIMEOUT_SECONDS]


def test_restaging_resets_timeout_and_keeps_latest_value(buffer, timers):
    buffer.stage("P100", 5)
    buffer.stage("P100", 6)
    assert buffer.pending_count == 1
    assert buffer._pending == {"P100": 6}
    assert len(_active(timers)) == 1
    assert timers[0]["cancelled"]


async def test_confirm_writes_everything_in_one_call(buffer, writer, timers):
    buffer.stage("P100", 1)
    buffer.stage("P101", 2)
    await buffer.confirm()
    assert writer.calls == [[("P100", 1), ("P101", 2)]]
    assert not buffer.has_pending
    assert not _active(timers)


async def test_confirm_with_nothing_staged_does_nothing(buffer, writer):
    await buffer.confirm()
    assert writer.calls == []


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), HanchuProtocolError("status=1"), HanchuLinkLostError("link dropped")],
)
async def test_failed_confirm_keeps_writes_for_retry(buffer, writer, timers, error):
    buffer.stage("P100", 1)
    writer.error = error
    with pytest.raises(type(error)):
        await buffer.confirm()
    assert buffer._pending == {"P100": 1}
    assert not buffer.is_confirming
    assert _active(timers), "auto-discard timer should still be running"

    writer.error = None
    await buffer.confirm()
    assert not buffer.has_pending


async def test_is_confirming_during_flush(buffer, writer):
    states = []
    buffer.add_listener(lambda: states.append(buffer.is_confirming))
    buffer.stage("P100", 1)
    writer.gate = asyncio.Event()
    task = asyncio.create_task(buffer.confirm())
    await writer.started.wait()
    assert buffer.is_confirming
    writer.gate.set()
    await task
    assert not buffer.is_confirming
    # stage -> False, flush start -> True, flush end -> False
    assert states == [False, True, False]


async def test_value_staged_during_flush_is_not_lost(buffer, writer, timers):
    # v1.4.1 race: Predbat stages a charge slot a few seconds after another
    # automation started a flush. Previously the whole buffer was cleared.
    buffer.stage("P100", 1)
    writer.gate = asyncio.Event()
    task = asyncio.create_task(buffer.confirm())
    await writer.started.wait()

    buffer.stage("P200", 9)  # new key mid-flush
    buffer.stage("P100", 2)  # same key, new value, mid-flush
    writer.gate.set()
    await task

    assert writer.calls == [[("P100", 1)]]
    assert buffer._pending == {"P100": 2, "P200": 9}
    assert _active(timers), "remaining edits must keep their auto-discard timer"


async def test_concurrent_confirms_are_serialised(buffer, writer):
    # v1.4.1 race: a second confirm arriving mid-flush used to return early
    # and be reported as "Confirmed" for values that were never sent.
    buffer.stage("P100", 1)
    writer.gate = asyncio.Event()
    first = asyncio.create_task(buffer.confirm())
    await writer.started.wait()

    buffer.stage("P200", 9)
    second = asyncio.create_task(buffer.confirm())
    await asyncio.sleep(0)
    assert len(writer.calls) == 1, "second confirm must wait for the first"

    writer.gate.set()
    await asyncio.gather(first, second)
    assert writer.calls == [[("P100", 1)], [("P200", 9)]]
    assert not buffer.has_pending


async def test_discard_clears_and_cancels_timer(buffer, timers):
    listener = MagicMock()
    buffer.stage("P100", 1)
    buffer.add_listener(listener)
    buffer.discard()
    assert not buffer.has_pending
    assert not _active(timers)
    listener.assert_called_once()


def test_discard_with_nothing_staged_does_not_notify(buffer):
    listener = MagicMock()
    buffer.add_listener(listener)
    buffer.discard()
    listener.assert_not_called()


async def test_discard_ignored_while_confirming(buffer, writer):
    buffer.stage("P100", 1)
    writer.gate = asyncio.Event()
    task = asyncio.create_task(buffer.confirm())
    await writer.started.wait()
    buffer.stage("P200", 9)
    buffer.discard()
    assert buffer.is_pending("P200")
    writer.gate.set()
    await task
    assert buffer._pending == {"P200": 9}


def test_timeout_auto_discards(buffer, timers):
    buffer.stage("P100", 1)
    _active(timers)[0]["action"](None)
    assert not buffer.has_pending


def test_timeout_handler_is_a_callback(buffer):
    # Must run on the event loop; without @callback HA may run it in a
    # worker thread where async_write_ha_state silently fails.
    assert getattr(buffer._handle_timeout, "_hass_callback", False)
