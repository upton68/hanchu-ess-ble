"""HanchuBleClient end to end against a fake logger (no Bluetooth needed)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.hanchu_ess_ble import ble_client as ble_client_module
from custom_components.hanchu_ess_ble.ble_client import HanchuBleClient
from custom_components.hanchu_ess_ble.protocol import HanchuProtocolError

from .fake_inverter import FakeBleakClient, FakeLogger, patched_ble

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def logger() -> FakeLogger:
    return FakeLogger({"P071": 64, "P067": 53.12, "L020": "Europe/London"})


@pytest.fixture
def bleak(logger) -> FakeBleakClient:
    return FakeBleakClient(logger)


@pytest.fixture
def client() -> HanchuBleClient:
    return HanchuBleClient(MagicMock(), ADDRESS, "HC:L110TEST")


@pytest.fixture
def short_timeout():
    """Shrink the outer operation timeout so hang tests finish quickly."""
    with patch.object(ble_client_module, "_TOTAL_OPERATION_TIMEOUT", 0.2):
        yield


async def test_read_values_round_trip(client, bleak, logger):
    with patched_ble(bleak):
        reply = await client.async_read_values(["P071", "P067"])
    assert reply.as_dict() == {"P071": 64, "P067": 53.12}
    assert logger.read_requests == [["P071", "P067"]]
    assert bleak.disconnected


async def test_read_values_missing_key_is_absent(client, bleak):
    with patched_ble(bleak):
        reply = await client.async_read_values(["P071", "P999"])
    assert reply.as_dict() == {"P071": 64}


async def test_read_values_batches_large_requests(client, bleak, logger):
    keys = [f"P{n:03d}" for n in range(30)]
    logger.registers.update({key: n for n, key in enumerate(keys)})
    with patched_ble(bleak) as connect:
        reply = await client.async_read_values(keys)
    # Default batch size is 12: 30 keys -> 12 + 12 + 6, all in one connection.
    assert [len(batch) for batch in logger.read_requests] == [12, 12, 6]
    assert connect.await_count == 1
    assert reply.as_dict() == {key: n for n, key in enumerate(keys)}


async def test_each_operation_uses_a_fresh_session_key(client, bleak, logger):
    keys_seen = []
    with patched_ble(bleak):
        for _ in range(5):
            await client.async_read_values(["P071"])
            keys_seen.append(logger.session_key)
    assert len(set(keys_seen)) > 1


async def test_read_raises_when_device_not_found(client, bleak):
    with patched_ble(bleak, device_found=False):
        with pytest.raises(BleakError, match="No connectable BLE device"):
            await client.async_read_values(["P071"])


async def test_write_value(client, bleak, logger):
    with patched_ble(bleak):
        reply = await client.async_write_value("P071", 50)
    assert reply.as_dict() == {"P071": 0}
    assert logger.registers["P071"] == 50


async def test_write_values_one_connection_in_order(client, bleak, logger):
    pairs = [("P100", 1), ("P101", 2), ("P102", 3)]
    with patched_ble(bleak) as connect:
        replies = await client.async_write_values(pairs)
    assert len(replies) == 3
    assert logger.write_requests == pairs
    assert connect.await_count == 1
    assert bleak.disconnected


async def test_write_values_stops_at_first_rejected_write(client, bleak, logger):
    logger.write_status["P101"] = 1
    with patched_ble(bleak):
        with pytest.raises(HanchuProtocolError, match="P101"):
            await client.async_write_values([("P100", 1), ("P101", 2), ("P102", 3)])
    # P102 never sent; connection still closed.
    assert logger.write_requests == [("P100", 1), ("P101", 2)]
    assert "P102" not in logger.registers
    assert bleak.disconnected


@pytest.mark.usefixtures("short_timeout")
async def test_hung_write_times_out_instead_of_blocking_forever(client, bleak):
    # Regression for the battery stalls fixed in v1.1.2: a GATT write that
    # never returns used to freeze the coordinator's update cycle silently.
    bleak.hang_on_write = asyncio.Event()
    with patched_ble(bleak):
        with pytest.raises(TimeoutError):
            await client.async_read_values(["P071"])


@pytest.mark.usefixtures("short_timeout")
async def test_hung_disconnect_times_out(client, bleak):
    bleak.hang_on_disconnect = asyncio.Event()
    with patched_ble(bleak):
        with pytest.raises(TimeoutError):
            await client.async_write_values([("P100", 1)])


@pytest.mark.usefixtures("short_timeout")
async def test_connection_lock_released_after_timeout(client, bleak):
    bleak.hang_on_write = asyncio.Event()
    with patched_ble(bleak):
        with pytest.raises(TimeoutError):
            await client.async_read_values(["P071"])
        bleak.hang_on_write = None
        reply = await client.async_read_values(["P071"])
    assert reply.as_dict() == {"P071": 64}


async def test_operations_are_serialised(client, bleak, logger):
    # Two callers at once (e.g. a poll and a Confirm Write) must not share a
    # connection: the second waits for the first to finish.
    active = 0
    peak = 0

    async def slow_connect(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        return bleak

    original_disconnect = bleak.disconnect

    async def disconnect():
        nonlocal active
        await original_disconnect()
        active -= 1

    bleak.disconnect = disconnect
    with patched_ble(bleak) as connect:
        connect.side_effect = slow_connect
        await asyncio.gather(
            client.async_read_values(["P071"]),
            client.async_write_values([("P100", 1)]),
            client.async_read_values(["P067"]),
        )
    assert peak == 1
    assert logger.read_requests == [["P071"], ["P067"]]
    assert logger.write_requests == [("P100", 1)]
