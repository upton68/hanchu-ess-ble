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


# --- Dropped links, reply timeouts and the read retry (v1.9.1) ----------------


@pytest.fixture
def fast_retry():
    """No pause before the retry, and short reply/close timeouts."""
    with (
        patch.object(ble_client_module, "_READ_RETRY_DELAY", 0),
        patch.object(ble_client_module, "_DEFAULT_OPERATION_TIMEOUT", 0.2),
        patch.object(ble_client_module, "_CLOSE_TIMEOUT", 0.2),
    ):
        yield


@pytest.fixture
def many_keys(logger):
    keys = [f"P{n:03d}" for n in range(30)]  # 3 batches of 12/12/6
    logger.registers.update({key: n for n, key in enumerate(keys)})
    return keys


@pytest.mark.usefixtures("fast_retry")
async def test_link_drop_mid_read_is_retried_and_succeeds(client, bleak, logger, many_keys):
    # What the debug logs showed: replies to the first batches, then the
    # link drops while waiting for a later one.
    bleak.drop_link_on_request = [3]
    with patched_ble(bleak) as connect:
        reply = await client.async_read_values(many_keys)
    assert reply.as_dict() == {key: n for n, key in enumerate(many_keys)}
    assert connect.await_count == 2
    assert client.stats.link_drops == 1
    assert client.stats.read_retries == 1
    assert client.stats.read_retries_succeeded == 1


@pytest.mark.usefixtures("fast_retry")
async def test_link_drop_fails_fast_instead_of_waiting_for_reply(client, bleak, many_keys):
    # Make the reply timeout long: a dropped link must not wait it out.
    bleak.drop_link_on_request = [2, 2]
    loop = asyncio.get_running_loop()
    with patch.object(ble_client_module, "_DEFAULT_OPERATION_TIMEOUT", 30), patched_ble(bleak):
        started = loop.time()
        with pytest.raises(ble_client_module.HanchuLinkLostError, match="read batch 2/3"):
            await client.async_read_values(many_keys)
    assert loop.time() - started < 2
    assert client.stats.link_drops == 2
    assert client.stats.read_retries_succeeded == 0


@pytest.mark.usefixtures("fast_retry")
async def test_reply_timeout_is_retried(client, bleak, many_keys):
    bleak.ignore_request = [2]
    with patched_ble(bleak) as connect:
        reply = await client.async_read_values(many_keys)
    assert len(reply.as_dict()) == 30
    assert connect.await_count == 2
    assert client.stats.reply_timeouts == 1


@pytest.mark.usefixtures("fast_retry")
async def test_reply_timeout_message_names_the_step(client, bleak, many_keys, caplog):
    bleak.ignore_request = [2, 2]
    with patched_ble(bleak):
        with pytest.raises(ble_client_module.HanchuReplyTimeoutError, match="read batch 2/3"):
            await client.async_read_values(many_keys)
    # A reply timeout is not a 60s hang and must not be logged as one.
    assert "exceeded" not in caplog.text


@pytest.mark.usefixtures("fast_retry")
async def test_stop_notify_skipped_on_dropped_link(client, bleak, many_keys):
    bleak.drop_link_on_request = [2]
    with patched_ble(bleak):
        await client.async_read_values(many_keys)
    # Only the successful retry stopped notifications.
    assert bleak.stop_notify_calls == 1


@pytest.mark.usefixtures("fast_retry")
async def test_writes_are_never_retried(client, bleak, logger):
    bleak.drop_link_on_request = [2]
    with patched_ble(bleak) as connect:
        with pytest.raises(ble_client_module.HanchuLinkLostError, match="write P101"):
            await client.async_write_values([("P100", 1), ("P101", 2), ("P102", 3)])
    assert connect.await_count == 1
    assert logger.write_requests == [("P100", 1)]


@pytest.mark.usefixtures("fast_retry")
async def test_single_write_link_drop_not_retried(client, bleak, logger):
    bleak.drop_link_on_request = [1]
    with patched_ble(bleak) as connect:
        with pytest.raises(ble_client_module.HanchuLinkLostError):
            await client.async_write_value("P100", 1)
    assert connect.await_count == 1
    assert logger.write_requests == []


@pytest.mark.usefixtures("short_timeout")
async def test_overall_timeout_is_reported_and_counted(client, bleak, caplog):
    bleak.hang_on_write = asyncio.Event()
    with patched_ble(bleak) as connect:
        with pytest.raises(ble_client_module.HanchuOperationTimeoutError):
            await client.async_read_values(["P071"])
    assert client.stats.operation_timeouts == 1
    assert "exceeded" in caplog.text
    # A genuine hang is not retried (it would double the wait).
    assert connect.await_count == 1


async def test_late_disconnect_from_earlier_operation_is_ignored(client, bleak):
    with patched_ble(bleak):
        await client.async_read_values(["P071"])
        stale_callback = bleak.disconnected_callback
        # A disconnect notice for the finished operation arrives late.
        stale_callback(bleak)
        reply = await client.async_read_values(["P071"])
    assert reply.as_dict() == {"P071": 64}
    assert client.stats.link_drops == 0


@pytest.mark.usefixtures("fast_retry")
async def test_hung_disconnect_does_not_block_a_good_read(client, bleak):
    bleak.hang_on_disconnect = asyncio.Event()
    with patched_ble(bleak):
        reply = await client.async_read_values(["P071"])
    assert reply.as_dict() == {"P071": 64}


# --- Link drops that surface as a failed GATT write (soak test, 20 Nov) -------

# Error texts seen in the soak-test log when the link dropped just as a
# request was being sent.
SEND_ERRORS = [
    "Peripheral 94:E6:86:1D:6D:8E changed connection status while waiting for "
    "BluetoothGATTWriteResponse, BluetoothGATTErrorResponse: Insufficient authorization (8)",
    "Characteristic 0000ff02-0000-1000-8000-00805f9b34fb was not found!",
    "Bluetooth GATT Error address=94:E6:86:1D:6D:8E handle=45 error=133 description=Error",
]


@pytest.mark.usefixtures("fast_retry")
@pytest.mark.parametrize("message", SEND_ERRORS)
async def test_failed_send_mid_read_is_retried(client, bleak, many_keys, message):
    bleak.fail_send_on_request = {2: message}
    with patched_ble(bleak) as connect:
        reply = await client.async_read_values(many_keys)
    assert len(reply.as_dict()) == 30
    assert connect.await_count == 2
    assert client.stats.link_drops == 1
    assert client.stats.read_retries_succeeded == 1


@pytest.mark.usefixtures("fast_retry")
async def test_failed_send_error_names_step_and_cause(client, bleak, many_keys):
    # Fails on both attempts, so the error reaches the caller.
    bleak.fail_send_on_request = {2: SEND_ERRORS[0]}
    bleak.drop_link_on_request = [3]
    with patched_ble(bleak):
        with pytest.raises(ble_client_module.HanchuLinkLostError) as excinfo:
            await client.async_read_values(many_keys)
    assert client.stats.read_retries == 1
    assert client.stats.read_retries_succeeded == 0
    # First attempt's message kept the step and the original cause.
    assert client.stats.link_drops == 2
    assert "read batch 3/3" in str(excinfo.value)


@pytest.mark.usefixtures("fast_retry")
async def test_failed_send_message_includes_cause(client, bleak):
    bleak.fail_send_on_request = {1: SEND_ERRORS[0]}
    with patched_ble(bleak):
        with pytest.raises(ble_client_module.HanchuLinkLostError) as excinfo:
            await client.async_write_value("P100", 1)
    message = str(excinfo.value)
    assert "while sending write P100" in message
    assert "Insufficient authorization (8)" in message


@pytest.mark.usefixtures("fast_retry")
async def test_failed_send_during_write_is_not_retried(client, bleak, logger):
    bleak.fail_send_on_request = {2: SEND_ERRORS[0]}
    with patched_ble(bleak) as connect:
        with pytest.raises(ble_client_module.HanchuLinkLostError, match="sending write P101"):
            await client.async_write_values([("P100", 1), ("P101", 2)])
    assert connect.await_count == 1
    assert logger.write_requests == [("P100", 1)]
