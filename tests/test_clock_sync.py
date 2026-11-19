"""Clock sync (L094 / L096): decide, write, verify, and refuse safely."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.hanchu_ess_ble import clock_sync
from custom_components.hanchu_ess_ble.clock_sync import (
    ClockSyncError,
    async_sync_clock,
    dst_offset_minutes,
)
from custom_components.hanchu_ess_ble.protocol import HanchuDataPoint, HanchuReply

LONDON = ZoneInfo("Europe/London")
WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


# --- dst_offset_minutes -------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "when", "expected"),
    [
        ("Europe/London", WINTER, 0),
        ("Europe/London", SUMMER, 60),
        ("UTC", SUMMER, 0),
        ("Australia/Lord_Howe", WINTER, 30),  # half-hour DST, southern summer
        ("Australia/Lord_Howe", SUMMER, 0),
    ],
)
def test_dst_offset_minutes(zone, when, expected):
    assert dst_offset_minutes(ZoneInfo(zone), when) == expected


def test_dst_offset_around_uk_autumn_change():
    # Clocks went back at 01:00 UTC on 25 Oct 2026.
    assert dst_offset_minutes(LONDON, datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc)) == 60
    assert dst_offset_minutes(LONDON, datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == 0


@pytest.mark.parametrize(("raw", "expected"), [("60", 60), (60.0, 60), ("1.7e9", 1700000000),
                                               (None, None), ("", None), ("abc", None)])
def test_to_int(raw, expected):
    assert clock_sync._to_int(raw) == expected


# --- async_sync_clock ---------------------------------------------------------


class FakeClockInverter:
    """Inverter registers that clock_sync reads and writes."""

    def __init__(self, registers, *, apply_writes=True):
        self.registers = dict(registers)
        self.apply_writes = apply_writes
        self.writes: list[list[tuple[str, object]]] = []

    async def async_read_values(self, keys, *, encrypted=True):
        return HanchuReply(
            tid="1", act="1", cmd="local", code="0",
            data=[HanchuDataPoint(k, self.registers[k]) for k in keys if k in self.registers],
        )

    async def async_write_values(self, pairs, *, encrypted=True):
        self.writes.append(list(pairs))
        if self.apply_writes:
            # Registers come back as strings, as they do over BLE.
            self.registers.update({k: str(v) for k, v in pairs})
        return []


_address_counter = iter(range(1000))


def _coordinator(inverter, ha_zone="Europe/London"):
    return SimpleNamespace(
        # Unique per test: the module keeps one lock per address.
        address=f"AA:BB:CC:DD:EE:{next(_address_counter):02X}",
        hass=SimpleNamespace(config=SimpleNamespace(time_zone=ha_zone)),
        client=inverter,
        async_request_refresh=AsyncMock(),
    )


@pytest.fixture
def at():
    """Freeze clock_sync's view of 'now' at a given UTC datetime."""

    def _freeze(moment: datetime, zone: str = "Europe/London"):
        epoch = moment.timestamp()
        stack = [
            patch.object(clock_sync.time, "time", return_value=epoch),
            patch.object(
                clock_sync, "dst_offset_minutes",
                side_effect=lambda tz, when=None: dst_offset_minutes(tz, when or moment),
            ),
            patch.object(clock_sync.dt_util, "get_default_time_zone", return_value=ZoneInfo(zone)),
        ]
        for p in stack:
            p.start()
        return int(epoch)

    yield _freeze
    patch.stopall()


async def test_already_correct_writes_nothing(at):
    now = at(WINTER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now + 5), "L096": "0"})
    coordinator = _coordinator(inverter)
    result = await async_sync_clock(coordinator, None)
    assert inverter.writes == []
    assert result["written"] == []
    assert result["before"]["drift_seconds"] == 5
    coordinator.async_request_refresh.assert_not_awaited()


async def test_wrong_dst_in_summer_writes_60_only(at):
    now = at(SUMMER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now), "L096": "0"})
    result = await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == [[("L096", 60)]]
    assert result["written"] == ["L096"]
    assert result["after"]["L096"] == 60
    assert result["message"] == "Inverter clock synced and verified"


async def test_drifted_rtc_writes_epoch(at):
    now = at(WINTER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now - 600), "L096": "0"})
    result = await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == [[("L094", now)]]
    assert result["before"]["drift_seconds"] == -600
    assert result["after"]["drift_seconds"] == 0


async def test_drift_within_tolerance_is_left_alone(at):
    now = at(WINTER)
    tolerance = clock_sync.CLOCK_DRIFT_TOLERANCE_SECONDS
    inverter = FakeClockInverter(
        {"L020": "Europe/London", "L094": str(now - tolerance), "L096": "0"}
    )
    await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == []


async def test_missing_epoch_is_written(at):
    now = at(WINTER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L096": "0"})
    await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == [[("L094", now)]]


async def test_force_writes_offset_then_epoch(at):
    now = at(WINTER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now), "L096": "0"})
    await async_sync_clock(_coordinator(inverter), None, force=True)
    # Order matters: offset first, then epoch (the order 1ulk tested).
    assert inverter.writes == [[("L096", 0), ("L094", now)]]


async def test_dry_run_reports_without_writing(at):
    now = at(SUMMER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now - 600), "L096": "0"})
    result = await async_sync_clock(_coordinator(inverter), None, dry_run=True)
    assert inverter.writes == []
    assert result["would_write"] == ["L096", "L094"]
    assert "written" not in result


async def test_refuses_when_zone_differs(at):
    now = at(SUMMER)
    inverter = FakeClockInverter({"L020": "Europe/Berlin", "L094": str(now), "L096": "0"})
    with pytest.raises(ClockSyncError, match="time zone"):
        await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == []


async def test_zone_with_whitespace_still_matches(at):
    now = at(WINTER)
    inverter = FakeClockInverter({"L020": " Europe/London ", "L094": str(now), "L096": "0"})
    await async_sync_clock(_coordinator(inverter), None)
    assert inverter.writes == []


async def test_refuses_during_confirm_write(at):
    at(WINTER)
    inverter = FakeClockInverter({"L020": "Europe/London", "L096": "60"})
    pending = SimpleNamespace(is_confirming=True)
    with pytest.raises(ClockSyncError, match="Confirm Write"):
        await async_sync_clock(_coordinator(inverter), pending)
    assert inverter.writes == []


async def test_failed_read_back_raises(at):
    now = at(SUMMER)
    inverter = FakeClockInverter(
        {"L020": "Europe/London", "L094": str(now), "L096": "0"}, apply_writes=False
    )
    coordinator = _coordinator(inverter)
    with pytest.raises(ClockSyncError, match="read-back"):
        await async_sync_clock(coordinator, None)
    # Still refreshes so entities show the real (unchanged) state.
    coordinator.async_request_refresh.assert_awaited_once()


async def test_second_sync_while_running_is_refused(at):
    now = at(WINTER)
    gate = asyncio.Event()
    inverter = FakeClockInverter({"L020": "Europe/London", "L094": str(now), "L096": "60"})
    original_read = inverter.async_read_values

    async def slow_read(keys, *, encrypted=True):
        await gate.wait()
        return await original_read(keys, encrypted=encrypted)

    inverter.async_read_values = slow_read
    coordinator = _coordinator(inverter)
    first = asyncio.create_task(async_sync_clock(coordinator, None))
    await asyncio.sleep(0)
    with pytest.raises(ClockSyncError, match="already running"):
        await async_sync_clock(coordinator, None)
    gate.set()
    await first
