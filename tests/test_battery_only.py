"""Battery-only entries: Hanchu batteries with a third-party inverter.

Covers the config flow (offered when no Hanchu inverter is in range), the
options and reconfigure flows for such an entry, setup with only battery
sensors, sequential polling, the services and diagnostics.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.hanchu_ess_ble.const import (
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_BATTERY_SCAN_INTERVAL,
    CONF_DEVICE_NAME,
    CONF_MODE,
    DEFAULT_BATTERY_ONLY_NAME,
    DOMAIN,
    MODE_BATTERY_ONLY,
)
from custom_components.hanchu_ess_ble.coordinator import HanchuBatteryStation
from custom_components.hanchu_ess_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .integration_setup import (
    BATTERY,
    BATTERY_REGISTERS,
    INVERTER,
    FakeHanchuClient,
    patched_bluetooth,
    service_info,
)

BATTERY_2 = "AA:BB:CC:DD:EE:03"
DISCOVERED = "custom_components.hanchu_ess_ble.config_flow.bluetooth.async_discovered_service_info"


def _battery_only_entry(hass, batteries=(BATTERY, BATTERY_2), interval=60) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=DEFAULT_BATTERY_ONLY_NAME,
        data={CONF_MODE: MODE_BATTERY_ONLY, CONF_DEVICE_NAME: DEFAULT_BATTERY_ONLY_NAME},
        options={
            CONF_BATTERY_ADDRESSES: list(batteries),
            CONF_BATTERY_SCAN_INTERVAL: interval,
        },
        unique_id=sorted(batteries)[0],
    )
    entry.add_to_hass(hass)
    return entry


# --- Config flow ---------------------------------------------------------------------


@pytest.fixture
def flow_env(hass, enable_custom_integrations):
    """Don't set the entry up; let each test say what's advertising."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("bluetooth")
    advertising: list = []
    with (
        patch("custom_components.hanchu_ess_ble.async_setup_entry", return_value=True),
        patch("custom_components.hanchu_ess_ble.async_unload_entry", return_value=True),
        patch(DISCOVERED, side_effect=lambda *_a, **_k: list(advertising)),
    ):
        yield advertising


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_inverter_in_range_still_goes_to_inverter_form(hass, flow_env):
    flow_env += [service_info(INVERTER, "HC:L110TEST"), service_info(BATTERY, "HC:L101BAT1")]
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_no_inverter_offers_batteries(hass, flow_env):
    flow_env += [
        service_info(BATTERY, "HC:L101BAT1"),
        service_info(BATTERY_2, "HC:L101BAT2"),
        service_info("AA:BB:CC:DD:EE:99", "Some Other Device"),
    ]
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "battery_only"
    choices = result["data_schema"].schema[CONF_BATTERY_ADDRESSES].options
    assert set(choices) == {BATTERY, BATTERY_2}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_NAME: "Garage Batteries", CONF_BATTERY_ADDRESSES: [BATTERY_2, BATTERY]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Batteries"
    assert result["data"] == {CONF_MODE: MODE_BATTERY_ONLY, CONF_DEVICE_NAME: "Garage Batteries"}
    assert result["options"] == {
        CONF_BATTERY_ADDRESSES: [BATTERY_2, BATTERY],
        CONF_BATTERY_SCAN_INTERVAL: 60,
    }
    assert result["result"].unique_id == BATTERY


async def test_battery_only_needs_a_battery(hass, flow_env):
    flow_env.append(service_info(BATTERY, "HC:L101BAT1"))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_NAME: "X", CONF_BATTERY_ADDRESSES: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_batteries_selected"}


async def test_nothing_in_range_aborts(hass, flow_env):
    flow_env.append(service_info("AA:BB:CC:DD:EE:99", "Some Other Device"))
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_batteries_in_another_entry_are_not_offered(hass, flow_env):
    # BATTERY already belongs to an inverter entry; only BATTERY_2 is free.
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Hanchu ESS"},
        options={CONF_BATTERY_ADDRESSES: [BATTERY]},
        unique_id=INVERTER,
    ).add_to_hass(hass)
    flow_env += [service_info(BATTERY, "HC:L101BAT1"), service_info(BATTERY_2, "HC:L101BAT2")]
    result = await _start(hass)
    assert list(result["data_schema"].schema[CONF_BATTERY_ADDRESSES].options) == [BATTERY_2]


async def test_all_batteries_already_configured_aborts(hass, flow_env):
    _battery_only_entry(hass)
    flow_env += [service_info(BATTERY, "HC:L101BAT1"), service_info(BATTERY_2, "HC:L101BAT2")]
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options_battery_only_sets_interval(hass, flow_env):
    entry = _battery_only_entry(hass, batteries=[BATTERY])
    flow_env += [service_info(BATTERY, "HC:L101BAT1"), service_info(BATTERY_2, "HC:L101BAT2")]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert CONF_BATTERY_SCAN_INTERVAL in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_BATTERY_ADDRESSES: [BATTERY, BATTERY_2], CONF_BATTERY_SCAN_INTERVAL: 120},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_BATTERY_ADDRESSES: [BATTERY, BATTERY_2],
        CONF_BATTERY_SCAN_INTERVAL: 120,
    }


async def test_options_battery_only_rejects_short_interval(hass, flow_env):
    entry = _battery_only_entry(hass, batteries=[BATTERY])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(Exception):  # noqa: B017 — voluptuous range error
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_BATTERY_ADDRESSES: [BATTERY], CONF_BATTERY_SCAN_INTERVAL: 10},
        )


async def test_options_battery_only_needs_a_battery(hass, flow_env):
    entry = _battery_only_entry(hass, batteries=[BATTERY])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BATTERY_ADDRESSES: [], CONF_BATTERY_SCAN_INTERVAL: 60}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_batteries_selected"}
    assert entry.options[CONF_BATTERY_ADDRESSES] == [BATTERY]


async def test_options_inverter_entry_has_no_interval(hass, flow_env):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Hanchu ESS"},
        unique_id=INVERTER,
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert CONF_BATTERY_SCAN_INTERVAL not in result["data_schema"].schema


async def test_reconfigure_battery_only_aborts(hass, flow_env):
    entry = _battery_only_entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "battery_only_reconfigure"


def test_new_flow_text_exists():
    import json
    from pathlib import Path

    import custom_components.hanchu_ess_ble as integration

    folder = Path(integration.__file__).parent
    strings = json.loads((folder / "translations" / "en.json").read_text())
    assert "battery_only" in strings["config"]["step"]
    assert "no_batteries_selected" in strings["config"]["error"]
    assert "no_batteries_selected" in strings["options"]["error"]
    for key in ("no_devices_found", "battery_only_reconfigure"):
        assert key in strings["config"]["abort"], key
    # strings.json is kept identical to en.json.
    assert json.loads((folder / "strings.json").read_text()) == strings


# --- Setup and polling ----------------------------------------------------------------


@pytest.fixture
async def station(hass, enable_custom_integrations):
    """A battery-only entry with two batteries, set up."""
    hass.config.components.add("bluetooth_adapters")
    clients = {
        BATTERY: FakeHanchuClient(dict(BATTERY_REGISTERS)),
        BATTERY_2: FakeHanchuClient({**BATTERY_REGISTERS, "B034": "40.0"}),
    }
    with patched_bluetooth(clients):
        entry = _battery_only_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, clients
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_setup_creates_only_battery_devices_and_sensors(hass, station):
    entry, _ = station
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(hass.data[DOMAIN][entry.entry_id], HanchuBatteryStation)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert {next(iter(d.identifiers))[1] for d in devices} == {BATTERY, BATTERY_2}

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert entities
    assert {e.domain for e in entities} == {"sensor"}
    assert all(e.unique_id.startswith((BATTERY, BATTERY_2)) for e in entities)

    soc = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{BATTERY_2}_b034")
    assert float(hass.states.get(soc).state) == 40.0


async def test_batteries_polled_on_the_entry_interval(hass, station):
    _, clients = station
    before = {a: c.reads for a, c in clients.items()}
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert all(clients[a].reads == before[a] + 1 for a in clients)


async def test_batteries_are_read_one_at_a_time(hass, station):
    entry, clients = station
    active = 0
    peak = 0

    def slow(client):
        original = client.async_read_values

        async def read(keys, *, encrypted=True):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            try:
                return await original(keys, encrypted=encrypted)
            finally:
                active -= 1

        client.async_read_values = read

    for client in clients.values():
        slow(client)
    await hass.data[DOMAIN][entry.entry_id]._async_poll_all()
    assert peak == 1


async def test_overlapping_cycle_is_skipped(hass, station):
    entry, clients = station
    station_obj = hass.data[DOMAIN][entry.entry_id]
    before = clients[BATTERY].reads
    async with station_obj._poll_lock:  # a cycle still running
        await station_obj._async_poll_all()
    assert clients[BATTERY].reads == before


async def test_one_failing_battery_does_not_stop_the_other(hass, station):
    entry, clients = station
    clients[BATTERY].read_error = TimeoutError("no reply")
    before = clients[BATTERY_2].reads
    await hass.data[DOMAIN][entry.entry_id]._async_poll_all()
    assert clients[BATTERY_2].reads == before + 1


async def test_unload_stops_polling(hass, station):
    entry, clients = station
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data
    before = {a: c.reads for a, c in clients.items()}
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=15))
    await hass.async_block_till_done()
    assert {a: c.reads for a, c in clients.items()} == before


async def test_battery_not_advertising_retries_setup(hass, enable_custom_integrations):
    hass.config.components.add("bluetooth_adapters")
    clients = {
        BATTERY: FakeHanchuClient(dict(BATTERY_REGISTERS)),
        BATTERY_2: FakeHanchuClient(dict(BATTERY_REGISTERS)),
    }
    with patched_bluetooth(clients) as bluetooth:
        bluetooth.async_last_service_info.side_effect = lambda _h, address, **_: (
            service_info(address, "HC:L101TEST") if address == BATTERY else None
        )
        entry = _battery_only_entry(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
        # The battery set up before the missing one released its listeners.
        unsubscribe = bluetooth.async_register_callback.return_value
        assert unsubscribe.call_count == bluetooth.async_register_callback.call_count


# --- Services and diagnostics ---------------------------------------------------------


async def _call(hass, service, data):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def test_read_registers_from_a_battery(hass, station):
    result = await _call(hass, "read_registers", {"keys": ["B034"], "battery_address": BATTERY_2})
    assert result["success"] is True
    assert result["values"] == {"B034": "40.0"}


async def test_read_registers_without_battery_address(hass, station):
    result = await _call(hass, "read_registers", {"keys": ["P071"]})
    assert result["success"] is False
    assert "battery_address" in result["message"]


async def test_confirm_write_and_sync_clock_need_an_inverter(hass, station):
    assert (await _call(hass, "confirm_write", {}))["success"] is False
    assert (await _call(hass, "sync_clock", {}))["success"] is False


async def test_diagnostics_battery_only(hass, station):
    import json

    entry, _ = station
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["mode"] == "battery_only"
    assert diag["battery_scan_interval"] == 60
    assert "inverter" not in diag
    assert set(diag["batteries"]) == {"battery_1", "battery_2"}
    dumped = json.dumps(diag, default=repr)
    assert BATTERY not in dumped and BATTERY_2 not in dumped
