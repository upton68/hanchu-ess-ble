"""Integration setup, unload and the confirm_write / read_registers / sync_clock services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.hanchu_ess_ble.const import CONF_BATTERY_ADDRESSES, DOMAIN
from custom_components.hanchu_ess_ble.models import (
    battery_model_from_values,
    model_from_values,
)

from .integration_setup import BATTERY, BATTERY_REGISTERS, INVERTER, INVERTER_REGISTERS


def _device(hass, entry_id, address):
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        if (DOMAIN, address) in device.identifiers:
            return device
    return None


async def test_setup_creates_devices_and_entities(harness):
    assert harness.entry.state is ConfigEntryState.LOADED
    assert _device(harness.hass, harness.entry.entry_id, INVERTER) is not None
    assert _device(harness.hass, harness.entry.entry_id, BATTERY) is not None

    # One control from each platform, showing the fake inverter's registers.
    assert harness.state("switch", "inverter_power").state == "on"
    assert harness.state("select", "work_mode").state == "Self-consumption"
    assert float(harness.state("number", "charge_power_limit").state) == 4000
    assert harness.state("time", "charge_slot_1_start").state == "00:00:00"


async def test_device_models_come_from_registers(harness):
    hass, entry_id = harness.hass, harness.entry.entry_id
    assert _device(hass, entry_id, INVERTER).model == model_from_values(INVERTER_REGISTERS).name
    assert _device(hass, entry_id, BATTERY).model == battery_model_from_values(BATTERY_REGISTERS)


async def test_unload_cleans_up_and_stops_polling(harness):
    hass, entry = harness.hass, harness.entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data

    # Nothing keeps polling the devices after unload.
    reads_before = (harness.inverter.reads, harness.battery.reads)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=15))
    await hass.async_block_till_done()
    assert (harness.inverter.reads, harness.battery.reads) == reads_before


async def test_polling_runs_while_loaded(harness):
    """Control for the unload test: the same time jump does poll when loaded."""
    reads_before = (harness.inverter.reads, harness.battery.reads)
    async_fire_time_changed(harness.hass, dt_util.utcnow() + timedelta(minutes=15))
    await harness.hass.async_block_till_done()
    assert harness.inverter.reads > reads_before[0]
    assert harness.battery.reads > reads_before[1]


async def test_reload_sets_up_again(harness):
    hass, entry = harness.hass, harness.entry
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert harness.state("switch", "inverter_power").state == "on"


# --- confirm_write -------------------------------------------------------------


async def _confirm(hass):
    return await hass.services.async_call(
        DOMAIN, "confirm_write", {}, blocking=True, return_response=True
    )


async def test_confirm_write_with_nothing_staged(harness):
    assert await _confirm(harness.hass) == {"success": True, "message": "Nothing staged to confirm"}
    assert harness.inverter.writes == []


async def test_confirm_write_flushes_staged_values(harness):
    harness.pending_writes.stage("L017", 3000)
    harness.pending_writes.stage("P651", 2)
    assert await _confirm(harness.hass) == {"success": True, "message": "Confirmed"}
    assert harness.inverter.writes == [("L017", 3000), ("P651", 2)]
    assert not harness.pending_writes.has_pending


async def test_confirm_write_failure_reported_and_kept_staged(harness):
    harness.pending_writes.stage("L017", 3000)
    harness.inverter.write_error = TimeoutError("No reply to write L017 within 10s")
    result = await _confirm(harness.hass)
    assert result["success"] is False
    assert "No reply to write L017" in result["message"]
    assert harness.pending_writes.is_pending("L017")


async def test_confirm_write_rejected_by_device(harness):
    harness.pending_writes.stage("L017", 3000)
    harness.inverter.write_status["L017"] = 1
    result = await _confirm(harness.hass)
    assert result["success"] is False
    assert harness.pending_writes.is_pending("L017")


# --- read_registers ------------------------------------------------------------


async def harness_call(hass, service, data):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def test_read_registers_inverter(harness):
    result = await harness_call(harness.hass, "read_registers", {"keys": "p500, l108 ,P999"})
    assert result["success"] is True
    assert result["device"] == f"inverter {INVERTER}"
    assert result["values"] == {"P500": 1, "L108": 0}
    assert result["missing"] == ["P999"]


async def test_read_registers_battery(harness):
    result = await harness_call(
        harness.hass, "read_registers", {"keys": ["B183"], "battery_address": BATTERY.lower()}
    )
    assert result["success"] is True
    assert result["device"] == f"battery {BATTERY}"
    assert result["values"] == {"B183": 1}


async def test_read_registers_unknown_battery(harness):
    result = await harness_call(
        harness.hass, "read_registers", {"keys": ["B183"], "battery_address": "11:22:33:44:55:66"}
    )
    assert result["success"] is False
    assert harness.battery.writes == []


async def test_read_registers_needs_keys(harness):
    result = await harness_call(harness.hass, "read_registers", {"keys": " , "})
    assert result == {"success": False, "message": "No register keys given"}


async def test_read_registers_reports_ble_failure(harness):
    harness.inverter.read_error = TimeoutError("No reply to read batch 1/1 within 10s")
    result = await harness_call(harness.hass, "read_registers", {"keys": "P500"})
    assert result["success"] is False
    assert "No reply" in result["message"]


# --- sync_clock ----------------------------------------------------------------


async def test_sync_clock_is_a_dry_run_by_default(harness):
    hass = harness.hass
    await hass.config.async_set_time_zone("Europe/London")
    harness.inverter.registers.update({"L020": "Europe/London", "L094": "0", "L096": "0"})
    result = await harness_call(hass, "sync_clock", {})
    assert result["success"] is True
    assert result["dry_run"] is True
    assert "L094" in result["would_write"]
    assert harness.inverter.writes == []


async def test_sync_clock_refuses_on_zone_mismatch(harness):
    hass = harness.hass
    await hass.config.async_set_time_zone("Europe/London")
    harness.inverter.registers.update({"L020": "Europe/Berlin", "L094": "0", "L096": "0"})
    result = await harness_call(hass, "sync_clock", {"dry_run": False})
    assert result["success"] is False
    assert "time zone" in result["message"]
    assert harness.inverter.writes == []


async def test_options_change_reloads_with_new_battery_selection(harness):
    hass, entry = harness.hass, harness.entry
    assert [b.address for b in harness.coordinator.battery_coordinators] == [BATTERY]

    with patch(
        "custom_components.hanchu_ess_ble.config_flow.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BATTERY_ADDRESSES: []}
        )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert harness.coordinator.battery_coordinators == []
