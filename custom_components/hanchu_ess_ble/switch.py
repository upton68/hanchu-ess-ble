"""Switch platform for Hanchu ESS BLE — inverter power (P500)."""

from __future__ import annotations

import logging
import time
from typing import Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SWITCH_MIN_TRANSITION_SECONDS, SWITCH_TRANSITION_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

POWER_KEY = "P500"  # Grid Relay / Inverter Power State — 0 = Off, 1 = On


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([InverterPowerSwitch(coordinator, entry)])


class InverterPowerSwitch(CoordinatorEntity, SwitchEntity):
    """On/Off switch for inverter power state (P500).

    Unlike the number/select/time entities, this writes immediately via
    async_write_value rather than staging through PendingWriteBuffer —
    batching a power-cycle command alongside unrelated staged setting
    changes would be confusing and risky for something this consequential.

    IMPORTANT: setting this Off stops solar production, battery charge/
    discharge, and EPS/backup output — not just grid import/export. On
    real hardware this takes roughly 50 seconds and several relay clicks
    to fully complete in either direction, during which the physical
    device panel goes completely blank (confirmed via testing by
    PaulDGAL, Sept 2026) with no partial/intermediate indication. This
    entity tracks that as an explicit "transitioning" state rather than
    just showing stale on/off, since the hardware itself gives no useful
    feedback during the transition.
    """

    _attr_has_entity_name = True
    _attr_name = "Inverter Power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_inverter_power"
        self._transitioning = False
        self._pending_command: int | None = None
        self._transition_started_at: float | None = None
        self._cancel_timeout: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.configured_name,
            manufacturer="Hanchu",
            model="ESS Device (Local BLE)",
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if inverter is ON (P500 = 1)."""
        if not self.coordinator.data or not self.coordinator.data.values:
            return None
        value = self.coordinator.data.values.get(POWER_KEY)
        if value is None:
            return None
        try:
            return int(float(value)) == 1
        except (ValueError, TypeError):
            return None

    @property
    def available(self) -> bool:
        """Unavailable while a command is mid-transition, to block repeat presses."""
        return not self._transitioning

    @property
    def icon(self) -> str:
        return "mdi:timer-sand" if self._transitioning else "mdi:power"

    @property
    def extra_state_attributes(self) -> dict:
        return {"transitioning": self._transitioning}

    async def async_turn_on(self, **kwargs) -> None:
        """Send P500 = 1 over BLE."""
        await self._async_set_power(1)

    async def async_turn_off(self, **kwargs) -> None:
        """Send P500 = 0 over BLE."""
        await self._async_set_power(0)

    async def _async_set_power(self, value: int) -> None:
        """Issue the write, then track the transition until it's confirmed.

        This entity does not use PendingWriteBuffer — the command is sent
        immediately via the client's existing single-key write path, the
        same one proven reliable elsewhere in this integration.
        """
        if self._transitioning:
            _LOGGER.warning(
                "Inverter power command already in progress; ignoring duplicate request"
            )
            return

        try:
            reply = await self.coordinator.client.async_write_value(
                POWER_KEY, value, encrypted=True
            )
        except Exception as err:
            _LOGGER.error("Failed to set inverter power: %s", err)
            return

        result = reply.as_dict().get(POWER_KEY)
        if result != 0:
            _LOGGER.error(
                "Inverter power write did not confirm success: %s", reply.as_dict()
            )
            return

        _LOGGER.info(
            "Inverter power command accepted (target=%s) — awaiting device transition",
            value,
        )
        self._pending_command = value
        self._transitioning = True
        self._transition_started_at = time.monotonic()
        self._start_transition_timeout()
        self.async_write_ha_state()

        # Request a refresh now rather than waiting for the next scheduled
        # poll — P500 is in FAST_POLL_KEYS, so the coordinator will pick up
        # the real value as soon as the device reports it, and
        # _handle_coordinator_update below clears the transitioning state
        # as soon as that happens (typically well inside the 90s safety
        # timeout, going by the ~50s observed on real hardware).
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear the transitioning state once the polled value confirms the
        command AND the minimum floor for that direction has elapsed.

        P500 was observed (real hardware, Sept 2026) to report the commanded
        value almost as soon as it's accepted — well before the inverter has
        actually finished physically completing the change, especially when
        turning on (~20s poll-confirmed vs ~50s actual restart time). Without
        the floor check, this would clear the transitioning state — and its
        unavailable/timer-sand UI — misleadingly early.
        """
        if (
            self._transitioning
            and self._pending_command is not None
            and self.coordinator.data
            and self.coordinator.data.values
        ):
            raw = self.coordinator.data.values.get(POWER_KEY)
            try:
                current = int(float(raw)) if raw is not None else None
            except (ValueError, TypeError):
                current = None

            elapsed = (
                time.monotonic() - self._transition_started_at
                if self._transition_started_at is not None
                else 0
            )
            min_floor = SWITCH_MIN_TRANSITION_SECONDS.get(self._pending_command, 0)

            if current == self._pending_command and elapsed >= min_floor:
                self._clear_transition()
            elif current == self._pending_command:
                _LOGGER.debug(
                    "Inverter power P500 already reads %s but only %.0fs "
                    "elapsed (floor=%ss for this direction) — still "
                    "treating as transitioning",
                    current,
                    elapsed,
                    min_floor,
                )
        super()._handle_coordinator_update()

    def _start_transition_timeout(self) -> None:
        self._cancel_transition_timeout()
        self._cancel_timeout = async_call_later(
            self.hass, SWITCH_TRANSITION_TIMEOUT_SECONDS, self._handle_transition_timeout
        )
        self.async_on_remove(self._cancel_transition_timeout)

    @callback
    def _handle_transition_timeout(self, _now) -> None:
        """Safety net: clear transitioning even if the expected value never showed up.

        This can happen if the write's own status reply was misleading, or
        the coordinator's polling hit its own consecutive-failure retries
        during the window. Either way, the switch should not be stuck
        unavailable forever — the log line makes the anomaly visible so
        it can be investigated, without leaving the entity unusable.
        """
        _LOGGER.warning(
            "Inverter power transition did not confirm within %ss; "
            "clearing transitioning state (device may not have reached "
            "the commanded state — check manually)",
            SWITCH_TRANSITION_TIMEOUT_SECONDS,
        )
        self._clear_transition()

    @callback
    def _clear_transition(self) -> None:
        self._transitioning = False
        self._pending_command = None
        self._transition_started_at = None
        self._cancel_transition_timeout()
        self.async_write_ha_state()

    def _cancel_transition_timeout(self) -> None:
        if self._cancel_timeout is not None:
            self._cancel_timeout()
            self._cancel_timeout = None
