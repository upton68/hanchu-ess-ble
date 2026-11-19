"""Coordinators: failure carry-forward, value merging, slow-key rotation,
model read, and battery self-scheduling (the v1.1.2 stall fix)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState, current_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from custom_components.hanchu_ess_ble.const import (
    BATTERY_POLL_KEYS,
    BATTERY_SCAN_INTERVAL,
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_DEVICE_NAME,
    DOMAIN,
    FAST_POLL_KEYS,
    MAX_CONSECUTIVE_FAILURES,
    SLOW_POLL_KEYS,
)
from custom_components.hanchu_ess_ble.coordinator import (
    HanchuBatteryCoordinator,
    HanchuBleCoordinator,
)
from custom_components.hanchu_ess_ble.models import MODEL_KEY, MODEL_READ_ATTEMPTS
from custom_components.hanchu_ess_ble.protocol import HanchuDataPoint, HanchuReply

INVERTER = "AA:BB:CC:DD:EE:01"
BATTERY = "AA:BB:CC:DD:EE:02"


def _service_info(address: str):
    return SimpleNamespace(
        address=address,
        name="HC:L110TEST",
        rssi=-60,
        connectable=True,
        time=datetime.now(timezone.utc),
        manufacturer_data={},
        service_data={},
    )


class FakeReader:
    """Answers async_read_values from a dict; can be told to fail."""

    def __init__(self, registers):
        self.registers = dict(registers)
        self.requests: list[list[str]] = []
        self.fail = False

    async def async_read_values(self, keys, *, encrypted=True):
        self.requests.append(list(keys))
        if self.fail:
            raise TimeoutError("simulated BLE failure")
        return HanchuReply(
            tid="1", act="1", cmd="local", code="0",
            data=[HanchuDataPoint(k, self.registers[k]) for k in keys if k in self.registers],
        )


@pytest.fixture
def bluetooth_mock():
    """Replace the bluetooth module the coordinators use."""
    mock = MagicMock()
    mock.async_last_service_info.side_effect = lambda _hass, address, **_: _service_info(address)
    mock.async_register_callback.return_value = MagicMock()
    mock.async_track_unavailable.return_value = MagicMock()
    with patch("custom_components.hanchu_ess_ble.coordinator.bluetooth", mock):
        yield mock


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "HC:L110TEST"},
        options={},
        unique_id=INVERTER,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def reader() -> FakeReader:
    registers = {key: 1 for key in FAST_POLL_KEYS}
    registers.update({key: f"slow-{key}" for key in SLOW_POLL_KEYS})
    registers[MODEL_KEY] = "HESS-HY-S-5.0K"
    return FakeReader(registers)


@pytest.fixture
def coordinator(hass, entry, bluetooth_mock, reader) -> HanchuBleCoordinator:
    coord = HanchuBleCoordinator(hass, entry)
    coord.client = reader
    return coord


async def test_successful_refresh(coordinator, reader):
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    data = coordinator.data
    assert data.values["P071"] == 1
    assert data.values[MODEL_KEY] == "HESS-HY-S-5.0K"
    assert data.consecutive_failures == 0
    assert data.last_successful_read is not None
    assert data.is_present
    assert data.rssi == -60


async def test_failures_below_threshold_keep_last_values(coordinator, reader):
    await coordinator.async_refresh()
    good_read = coordinator.data.last_successful_read

    reader.fail = True
    for attempt in range(1, MAX_CONSECUTIVE_FAILURES):
        await coordinator.async_refresh()
        assert coordinator.last_update_success, f"attempt {attempt} should not fail"
        assert coordinator.data.values["P071"] == 1
        assert coordinator.data.consecutive_failures == attempt
        assert coordinator.data.last_successful_read == good_read


async def test_failures_at_threshold_mark_update_failed(coordinator, reader):
    await coordinator.async_refresh()
    reader.fail = True
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_success_after_failures_resets_counter(coordinator, reader):
    await coordinator.async_refresh()
    reader.fail = True
    await coordinator.async_refresh()
    reader.fail = False
    await coordinator.async_refresh()
    assert coordinator.data.consecutive_failures == 0
    # Back to needing the full threshold before failing again.
    reader.fail = True
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_slow_keys_rotate_one_per_successful_cycle(coordinator, reader):
    for _ in range(len(SLOW_POLL_KEYS) + 1):
        await coordinator.async_refresh()
    poll_requests = [r for r in reader.requests if r != [MODEL_KEY]]
    for request in poll_requests:
        assert request[: len(FAST_POLL_KEYS)] == list(FAST_POLL_KEYS)
        assert len(request) == len(FAST_POLL_KEYS) + 1
    slow_seen = [request[-1] for request in poll_requests]
    assert slow_seen == list(SLOW_POLL_KEYS) + [SLOW_POLL_KEYS[0]]
    # Every slow key's value has accumulated in the merged state.
    for key in SLOW_POLL_KEYS:
        assert coordinator.data.values[key] == f"slow-{key}"


async def test_failed_cycle_does_not_advance_slow_key(coordinator, reader):
    await coordinator.async_refresh()
    reader.fail = True
    await coordinator.async_refresh()
    reader.fail = False
    await coordinator.async_refresh()
    poll_requests = [r for r in reader.requests if r != [MODEL_KEY]]
    assert [r[-1] for r in poll_requests] == [SLOW_POLL_KEYS[0], SLOW_POLL_KEYS[1], SLOW_POLL_KEYS[1]]


async def test_values_merge_and_timestamps_only_update_for_returned_keys(coordinator, reader):
    await coordinator.async_refresh()
    first_ts = dict(coordinator.data.last_updated)
    del reader.registers["P067"]  # not returned next cycle
    reader.registers["P071"] = 2
    await coordinator.async_refresh()
    data = coordinator.data
    assert data.values["P071"] == 2
    assert data.values["P067"] == 1  # carried over
    assert data.last_updated["P067"] == first_ts["P067"]
    assert data.last_updated["P071"] >= first_ts["P071"]


async def test_model_read_stops_once_known(coordinator, reader):
    for _ in range(3):
        await coordinator.async_refresh()
    assert reader.requests.count([MODEL_KEY]) == 1


async def test_model_read_gives_up_on_firmware_without_p003(coordinator, reader):
    del reader.registers[MODEL_KEY]
    for _ in range(10):
        await coordinator.async_refresh()
    assert reader.requests.count([MODEL_KEY]) == MODEL_READ_ATTEMPTS
    assert coordinator.last_update_success


async def test_no_advertisement_before_first_data_is_not_ready(coordinator, bluetooth_mock):
    bluetooth_mock.async_last_service_info.side_effect = None
    bluetooth_mock.async_last_service_info.return_value = None
    with pytest.raises(ConfigEntryNotReady):
        await coordinator._async_update_data()


async def test_advertisement_lost_keeps_values_marks_absent(coordinator, bluetooth_mock, reader):
    await coordinator.async_refresh()
    bluetooth_mock.async_last_service_info.side_effect = None
    bluetooth_mock.async_last_service_info.return_value = None
    requests_before = len(reader.requests)
    await coordinator.async_refresh()
    assert coordinator.data.is_present is False
    assert coordinator.data.values["P071"] == 1
    assert len(reader.requests) == requests_before  # no BLE attempt


# --- Battery coordinator ------------------------------------------------------


@pytest.fixture
def battery_reader() -> FakeReader:
    return FakeReader({key: 1 for key in BATTERY_POLL_KEYS})


@pytest.fixture
async def battery(hass, entry, bluetooth_mock, battery_reader):
    coord = HanchuBatteryCoordinator(hass, entry, BATTERY)
    coord.client = battery_reader
    yield coord
    await coord.async_shutdown()


async def test_battery_polls_all_keys_in_one_request(battery, battery_reader):
    await battery.async_refresh()
    assert battery_reader.requests == [list(BATTERY_POLL_KEYS)]
    assert battery.data.values["B034"] == 1


async def test_battery_uses_explicit_timer_not_base_scheduler(battery):
    # Passing update_interval as well caused overlapping refreshes (v1.1.2).
    assert battery.update_interval is None


async def test_battery_keeps_polling_on_its_own(hass, battery, battery_reader):
    # Regression for the v1.1.2 stall: battery data must keep refreshing
    # unattended, cycle after cycle, without a manual update_entity.
    await battery.async_refresh()
    await battery.async_setup()
    now = dt_util.utcnow()
    for cycle in range(1, 4):
        async_fire_time_changed(hass, now + BATTERY_SCAN_INTERVAL * cycle + timedelta(seconds=1))
        await hass.async_block_till_done()
    assert len(battery_reader.requests) == 4


async def test_battery_shutdown_stops_polling(hass, battery, battery_reader):
    await battery.async_refresh()
    await battery.async_setup()
    await battery.async_shutdown()
    async_fire_time_changed(hass, dt_util.utcnow() + BATTERY_SCAN_INTERVAL * 3)
    await hass.async_block_till_done()
    assert len(battery_reader.requests) == 1


async def test_battery_failure_carry_forward(battery, battery_reader):
    await battery.async_refresh()
    battery_reader.fail = True
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        await battery.async_refresh()
        assert battery.last_update_success
        assert battery.data.values["B034"] == 1
    await battery.async_refresh()
    assert not battery.last_update_success


async def test_inverter_setup_creates_battery_coordinators(
    hass, entry, bluetooth_mock, coordinator
):
    hass.config_entries.async_update_entry(entry, options={CONF_BATTERY_ADDRESSES: [BATTERY]})
    # In real setup HA sets current_entry while async_setup_entry runs and
    # the battery coordinators pick the entry up from it (they don't pass
    # config_entry explicitly). Reproduce that, and the entry state that
    # async_config_entry_first_refresh requires.
    current_entry.set(entry)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    with patch(
        "custom_components.hanchu_ess_ble.coordinator.HanchuBleClient",
        return_value=FakeReader({key: 1 for key in BATTERY_POLL_KEYS}),
    ):
        await coordinator.async_setup()
    assert [b.address for b in coordinator.battery_coordinators] == [BATTERY]
    await coordinator.async_shutdown()
    assert coordinator.battery_coordinators == []
