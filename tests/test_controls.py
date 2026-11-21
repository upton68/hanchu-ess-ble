"""Staged controls (number, select, time) with Confirm Write / Discard Changes,
and the Battery Recovery and Sync Clock buttons."""

from __future__ import annotations

import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hanchu_ess_ble.ble_client import HanchuLinkLostError
from custom_components.hanchu_ess_ble.const import BATTERY_RECOVERY_OFF, BATTERY_RECOVERY_ON
from custom_components.hanchu_ess_ble.protocol import HanchuProtocolError


async def _call(harness, domain, service, suffix, **data):
    await harness.hass.services.async_call(
        domain,
        service,
        {"entity_id": harness.entity_id(domain, suffix), **data},
        blocking=True,
    )
    await harness.hass.async_block_till_done()


async def _press(harness, suffix):
    await _call(harness, "button", "press", suffix)


async def _stage_three(harness):
    await _call(harness, "number", "set_value", "charge_power_limit", value=2500)
    await _call(harness, "select", "select_option", "work_mode", option="Backup Energy")
    await _call(harness, "time", "set_value", "charge_slot_1_start", time="02:30:00")


# --- Staging ----------------------------------------------------------------------


async def test_edits_are_staged_not_written(harness):
    await _stage_three(harness)
    assert harness.inverter.writes == []
    # The new values show straight away.
    assert float(harness.state("number", "charge_power_limit").state) == 2500
    assert harness.state("select", "work_mode").state == "Backup Energy"
    assert harness.state("time", "charge_slot_1_start").state == "02:30:00"
    assert harness.pending_writes.pending_count == 3


async def test_buttons_only_available_with_something_staged(harness):
    assert harness.state("button", "confirm_write").state == STATE_UNAVAILABLE
    assert harness.state("button", "discard_changes").state == STATE_UNAVAILABLE
    await _call(harness, "number", "set_value", "charge_power_limit", value=2500)
    assert harness.state("button", "confirm_write").state != STATE_UNAVAILABLE
    assert harness.state("button", "discard_changes").state != STATE_UNAVAILABLE


async def test_confirm_writes_everything_in_one_go(harness):
    await _stage_three(harness)
    await _press(harness, "confirm_write")

    # Values converted to what the inverter expects: watts, mode number,
    # seconds since midnight.
    assert harness.inverter.writes == [("L017", 2500), ("P651", 2), ("L005", 9000)]
    assert not harness.pending_writes.has_pending
    assert harness.state("button", "confirm_write").state == STATE_UNAVAILABLE
    # After the refresh, entities show the device's (now updated) values.
    assert float(harness.state("number", "charge_power_limit").state) == 2500
    assert harness.state("select", "work_mode").state == "Backup Energy"
    assert harness.state("time", "charge_slot_1_start").state == "02:30:00"


async def test_discard_reverts_without_writing(harness):
    await _stage_three(harness)
    await _press(harness, "discard_changes")
    assert harness.inverter.writes == []
    assert not harness.pending_writes.has_pending
    assert float(harness.state("number", "charge_power_limit").state) == 4000
    assert harness.state("select", "work_mode").state == "Self-consumption"
    assert harness.state("time", "charge_slot_1_start").state == "00:00:00"


async def test_failed_confirm_keeps_edits_staged(harness):
    await _stage_three(harness)
    harness.inverter.write_error = HanchuLinkLostError("BLE link dropped while sending write L017")
    with pytest.raises(HomeAssistantError, match="Confirm Write failed: BLE link dropped.*still staged"):
        await _press(harness, "confirm_write")
    assert harness.pending_writes.pending_count == 3
    assert float(harness.state("number", "charge_power_limit").state) == 2500

    # Retry once the link is back.
    harness.inverter.write_error = None
    await _press(harness, "confirm_write")
    assert not harness.pending_writes.has_pending
    assert harness.inverter.registers["P651"] == 2


async def test_partial_reject_keeps_unwritten_edits(harness):
    await _stage_three(harness)
    harness.inverter.write_status["P651"] = 1  # inverter rejects the work mode
    with pytest.raises(HomeAssistantError, match="Confirm Write failed: .*P651") as excinfo:
        await _press(harness, "confirm_write")
    assert isinstance(excinfo.value.__cause__, HanchuProtocolError)
    # Everything stays staged for a retry; nothing is silently dropped.
    assert harness.pending_writes.pending_count == 3


async def test_failed_confirm_error_never_blank(harness):
    # A bare TimeoutError has no message; the error must still say something.
    await _call(harness, "number", "set_value", "charge_power_limit", value=2500)
    harness.inverter.write_error = TimeoutError()
    with pytest.raises(HomeAssistantError, match="Confirm Write failed: TimeoutError"):
        await _press(harness, "confirm_write")


async def test_restaging_the_same_control_keeps_latest_value(harness):
    await _call(harness, "number", "set_value", "charge_power_limit", value=2500)
    await _call(harness, "number", "set_value", "charge_power_limit", value=3000)
    await _press(harness, "confirm_write")
    assert harness.inverter.writes == [("L017", 3000)]


# --- Number limits -----------------------------------------------------------------


async def test_power_limit_max_follows_the_inverter_model(harness):
    # HESS-HY-S-5.0K caps battery power at 5000 W.
    assert harness.state("number", "charge_power_limit").attributes["max"] == 5000


async def test_soc_limits_are_enforced(harness):
    with pytest.raises(ServiceValidationError):
        await _call(harness, "number", "set_value", "min_discharge_soc", value=60)
    assert harness.inverter.writes == []
    assert not harness.pending_writes.has_pending


# --- Battery Recovery (P511) -------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "value"),
    [("battery_recovery_start", BATTERY_RECOVERY_ON), ("battery_recovery_stop", BATTERY_RECOVERY_OFF)],
)
async def test_battery_recovery_buttons_write_immediately(harness, suffix, value):
    await _press(harness, suffix)
    assert harness.inverter.writes == [("P511", value)]
    assert not harness.pending_writes.has_pending  # not staged


async def test_battery_recovery_write_failure_raises(harness):
    harness.inverter.write_error = TimeoutError("No reply to write P511 within 10s")
    with pytest.raises(HomeAssistantError, match="Failed to write P511=10"):
        await _press(harness, "battery_recovery_start")


async def test_battery_recovery_rejected_raises(harness):
    harness.inverter.write_status["P511"] = 1
    with pytest.raises(HomeAssistantError, match="did not confirm P511=10"):
        await _press(harness, "battery_recovery_start")


# --- Sync Clock button --------------------------------------------------------------


async def test_sync_clock_button_refusal_is_shown(harness):
    await harness.hass.config.async_set_time_zone("Europe/London")
    harness.inverter.registers.update({"L020": "Europe/Berlin", "L094": "0", "L096": "0"})
    with pytest.raises(HomeAssistantError, match="Clock sync: .*time zone"):
        await _press(harness, "sync_clock")
    assert harness.inverter.writes == []
