"""Switches: Inverter Power (P500) and the battery preheating switches (L108, L114)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.hanchu_ess_ble.ble_client import HanchuLinkLostError
from custom_components.hanchu_ess_ble.const import (
    REGISTER_SWITCH_CONFIRM_TIMEOUT_SECONDS,
    SWITCH_MIN_TRANSITION_SECONDS,
    SWITCH_TRANSITION_TIMEOUT_SECONDS,
)


class Clock:
    """Stands in for time.monotonic inside switch.py only."""

    def __init__(self) -> None:
        self.start = 1000.0
        self.now = self.start

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock():
    clock = Clock()
    with patch(
        "custom_components.hanchu_ess_ble.switch.time",
        SimpleNamespace(monotonic=clock.monotonic),
    ):
        yield clock


async def _switch(harness, service: str, suffix: str) -> None:
    await harness.hass.services.async_call(
        "switch",
        service,
        {"entity_id": harness.entity_id("switch", suffix)},
        blocking=True,
    )


async def _advance(harness, clock: Clock, seconds: float) -> None:
    """Move both clocks on: the switch's monotonic one and HA's timers.

    HA's timers are fired relative to the real current time, so pass the
    total time moved so far, not just this step.
    """
    clock.now += seconds
    async_fire_time_changed(
        harness.hass, dt_util.utcnow() + timedelta(seconds=clock.now - clock.start)
    )
    await harness.hass.async_block_till_done()


# --- Inverter Power (P500) -----------------------------------------------------


async def test_power_off_writes_p500_and_shows_transition(harness, clock):
    await _switch(harness, "turn_off", "inverter_power")
    assert harness.inverter.writes == [("P500", 0)]
    # Unavailable while the inverter changes state, so it can't be pressed again.
    assert harness.state("switch", "inverter_power").state == STATE_UNAVAILABLE


async def test_power_off_waits_for_floor_even_if_p500_reads_back(harness, clock):
    await _switch(harness, "turn_off", "inverter_power")
    floor = SWITCH_MIN_TRANSITION_SECONDS[0]

    # P500 already reads 0, but the off floor hasn't passed.
    clock.now += floor - 1
    await harness.poll()
    assert harness.state("switch", "inverter_power").state == STATE_UNAVAILABLE

    clock.now += 2
    await harness.poll()
    state = harness.state("switch", "inverter_power")
    assert state.state == STATE_OFF
    assert state.attributes["transitioning"] is False


async def test_power_on_uses_the_longer_floor(harness, clock):
    harness.inverter.registers["P500"] = 0
    await harness.poll()
    await _switch(harness, "turn_on", "inverter_power")

    # Past the off floor but inside the on floor: P500 reading 1 is too early.
    clock.now += SWITCH_MIN_TRANSITION_SECONDS[0] + 1
    await harness.poll()
    assert harness.state("switch", "inverter_power").state == STATE_UNAVAILABLE

    clock.now += SWITCH_MIN_TRANSITION_SECONDS[1]
    await harness.poll()
    assert harness.state("switch", "inverter_power").state == STATE_ON


async def test_power_transition_times_out_if_never_confirmed(harness, clock, caplog):
    harness.inverter.ignore_writes.add("P500")  # accepted, but P500 stays 1
    await _switch(harness, "turn_off", "inverter_power")
    assert harness.state("switch", "inverter_power").state == STATE_UNAVAILABLE

    await _advance(harness, clock, SWITCH_TRANSITION_TIMEOUT_SECONDS + 1)
    assert harness.state("switch", "inverter_power").state == STATE_ON
    assert "did not confirm" in caplog.text


async def test_power_press_ignored_while_transitioning(harness, clock):
    await _switch(harness, "turn_off", "inverter_power")
    await _switch(harness, "turn_on", "inverter_power")
    assert harness.inverter.writes == [("P500", 0)]


async def test_power_write_failure_is_reported_and_switch_stays_usable(harness, clock):
    harness.inverter.write_error = HanchuLinkLostError("BLE link dropped while sending write P500")
    with pytest.raises(HomeAssistantError, match="Failed to set inverter power .*link dropped"):
        await _switch(harness, "turn_off", "inverter_power")
    state = harness.state("switch", "inverter_power")
    assert state.state == STATE_ON
    assert state.attributes["transitioning"] is False

    # Works on the next try once the link is back.
    harness.inverter.write_error = None
    await _switch(harness, "turn_off", "inverter_power")
    assert harness.inverter.registers["P500"] == 0


async def test_power_write_failure_message_never_blank(harness, clock):
    harness.inverter.write_error = TimeoutError()
    with pytest.raises(HomeAssistantError, match=r"Failed to set inverter power \(P500=0\): TimeoutError"):
        await _switch(harness, "turn_off", "inverter_power")


async def test_power_write_rejected_is_reported(harness, clock):
    harness.inverter.write_status["P500"] = 1
    with pytest.raises(HomeAssistantError, match="did not confirm power P500=0"):
        await _switch(harness, "turn_off", "inverter_power")
    state = harness.state("switch", "inverter_power")
    assert state.state == STATE_ON
    assert state.attributes["transitioning"] is False


# --- Preheating switches (L108, L114) ------------------------------------------


async def test_preheating_switch_writes_and_confirms(harness, clock):
    await _switch(harness, "turn_on", "intelligent_battery_preheating")
    assert harness.inverter.writes == [("L108", 1)]
    state = harness.state("switch", "intelligent_battery_preheating")
    assert state.state == STATE_ON
    # The write path refreshes straight away; L108 already reads 1.
    assert state.attributes["awaiting_confirmation"] is False


async def test_preheating_switch_shows_command_until_timeout_then_device_value(
    harness, clock, caplog
):
    harness.inverter.ignore_writes.add("L108")
    await _switch(harness, "turn_on", "intelligent_battery_preheating")
    state = harness.state("switch", "intelligent_battery_preheating")
    assert state.state == STATE_ON
    assert state.attributes["awaiting_confirmation"] is True

    await _advance(harness, clock, REGISTER_SWITCH_CONFIRM_TIMEOUT_SECONDS + 1)
    state = harness.state("switch", "intelligent_battery_preheating")
    assert state.state == STATE_OFF
    assert state.attributes["awaiting_confirmation"] is False
    assert "still reports 0" in caplog.text


async def test_manual_preheat_does_not_trust_an_early_read_back(harness, clock):
    # L114 can read 1 straight after the write and then be reverted by the
    # inverter (above 5°C), so it isn't confirmed before 30 seconds.
    await _switch(harness, "turn_on", "battery_preheating_manual")
    assert harness.inverter.registers["L114"] == 1
    assert harness.state("switch", "battery_preheating_manual").attributes[
        "awaiting_confirmation"
    ] is True

    clock.now += 29
    await harness.poll()
    assert harness.state("switch", "battery_preheating_manual").attributes[
        "awaiting_confirmation"
    ] is True

    clock.now += 2
    await harness.poll()
    state = harness.state("switch", "battery_preheating_manual")
    assert state.state == STATE_ON
    assert state.attributes["awaiting_confirmation"] is False


async def test_manual_preheat_reverted_by_inverter_shows_off(harness, clock, caplog):
    await _switch(harness, "turn_on", "battery_preheating_manual")
    # The inverter reverts it a few seconds later. Polls every 30 s see that,
    # but the switch keeps showing the command until the timeout.
    harness.inverter.registers["L114"] = 0
    polls_before = harness.inverter.reads
    for _ in range(2):  # polls at 30 s and 60 s
        await _advance(harness, clock, 30)
        assert harness.state("switch", "battery_preheating_manual").state == STATE_ON
    assert harness.inverter.reads > polls_before
    await _advance(harness, clock, REGISTER_SWITCH_CONFIRM_TIMEOUT_SECONDS - 60 + 1)
    assert harness.state("switch", "battery_preheating_manual").state == STATE_OFF
    assert "below 5°C" in caplog.text


async def test_preheating_write_failure_raises(harness, clock):
    harness.inverter.write_error = HanchuLinkLostError("BLE link dropped while sending write L108")
    with pytest.raises(HomeAssistantError, match="Failed to write L108=1"):
        await _switch(harness, "turn_on", "intelligent_battery_preheating")
    assert harness.state("switch", "intelligent_battery_preheating").state == STATE_OFF


async def test_preheating_write_rejected_raises(harness, clock):
    harness.inverter.write_status["L108"] = 1
    with pytest.raises(HomeAssistantError, match="did not confirm L108=1"):
        await _switch(harness, "turn_on", "intelligent_battery_preheating")
    assert harness.state("switch", "intelligent_battery_preheating").state == STATE_OFF
