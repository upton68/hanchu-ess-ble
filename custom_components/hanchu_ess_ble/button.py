"""Button entities for the Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BATTERY_RECOVERY_KEY,
    BATTERY_RECOVERY_OFF,
    BATTERY_RECOVERY_ON,
    DOMAIN,
)
from .coordinator import HanchuBleCoordinator
from .pending_writes import PendingWriteBuffer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the confirm/discard buttons for a config entry."""
    coordinator: HanchuBleCoordinator = hass.data[DOMAIN][entry.entry_id]
    pending_writes: PendingWriteBuffer = hass.data[DOMAIN][
        entry.entry_id + "_pending_writes"
    ]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, coordinator.address)},
        name=coordinator.configured_name,
    )

    async_add_entities(
        [
            HanchuConfirmWriteButton(pending_writes, device_info, coordinator.address),
            HanchuDiscardChangesButton(pending_writes, device_info, coordinator.address),
            HanchuBatteryRecoveryButton(
                coordinator,
                device_info,
                unique_suffix="battery_recovery_start",
                name="Battery Recovery Start",
                icon="mdi:battery-sync",
                value=BATTERY_RECOVERY_ON,
            ),
            HanchuBatteryRecoveryButton(
                coordinator,
                device_info,
                unique_suffix="battery_recovery_stop",
                name="Battery Recovery Stop",
                icon="mdi:battery-remove-outline",
                value=BATTERY_RECOVERY_OFF,
            ),
        ]
    )


class HanchuConfirmWriteButton(ButtonEntity):
    """Flushes all staged register writes as one BLE session."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:check-circle-outline"
    _attr_has_entity_name = True
    _attr_name = "Confirm Write"

    def __init__(
        self,
        pending_writes: PendingWriteBuffer,
        device_info: DeviceInfo,
        address: str,
    ) -> None:
        """Initialise the confirm button."""
        self._pending_writes = pending_writes
        self._attr_device_info = device_info
        self._attr_unique_id = f"{address}_confirm_write"
        pending_writes.add_listener(self._handle_pending_change)

    @property
    def available(self) -> bool:
        """Only actionable while there's something staged, and not already confirming."""
        return self._pending_writes.has_pending and not self._pending_writes.is_confirming

    def _handle_pending_change(self) -> None:
        """React to the buffer changing (staged, confirmed, or discarded)."""
        self.async_write_ha_state()

    async def async_press(self) -> None:
        """Flush the staged writes."""
        await self._pending_writes.confirm()


class HanchuDiscardChangesButton(ButtonEntity):
    """Clears staged edits without writing them to the device."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:close-circle-outline"
    _attr_has_entity_name = True
    _attr_name = "Discard Changes"

    def __init__(
        self,
        pending_writes: PendingWriteBuffer,
        device_info: DeviceInfo,
        address: str,
    ) -> None:
        """Initialise the discard button."""
        self._pending_writes = pending_writes
        self._attr_device_info = device_info
        self._attr_unique_id = f"{address}_discard_changes"
        pending_writes.add_listener(self._handle_pending_change)

    @property
    def available(self) -> bool:
        """Only actionable while there's something staged, and not mid-confirm."""
        return self._pending_writes.has_pending and not self._pending_writes.is_confirming

    def _handle_pending_change(self) -> None:
        """React to the buffer changing (staged, confirmed, or discarded)."""
        self.async_write_ha_state()

    async def async_press(self) -> None:
        """Discard the staged writes."""
        self._pending_writes.discard()


class HanchuBatteryRecoveryButton(ButtonEntity):
    """Writes P511 (Battery Recovery) — 10 to start, 5 to stop.

    A button rather than a switch because P511 reads back as null when no
    recovery is in progress (confirmed on real hardware, Oct 2026), so a
    switch would sit at "unknown" permanently. The raw value is exposed
    separately as the Battery Recovery Status diagnostic sensor.

    This is a rescue tool for a battery whose BMS has shut down after deep
    over-discharge; Hanchu's own menu notes it needs at least 500W of PV
    input to operate. It is not something to run routinely, so both buttons
    are disabled by default and write immediately (no Confirm Write step).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HanchuBleCoordinator,
        device_info: DeviceInfo,
        *,
        unique_suffix: str,
        name: str,
        icon: str,
        value: int,
    ) -> None:
        """Initialise the recovery button."""
        self._coordinator = coordinator
        self._value = value
        self._attr_device_info = device_info
        self._attr_unique_id = f"{coordinator.address}_{unique_suffix}"
        self._attr_name = name
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Write the recovery value to the inverter."""
        key = BATTERY_RECOVERY_KEY
        try:
            reply = await self._coordinator.client.async_write_value(
                key, self._value, encrypted=True
            )
        except Exception as err:  # noqa: BLE001 — surface any failure to the UI
            raise HomeAssistantError(f"Failed to write {key}={self._value}: {err}") from err

        status = reply.as_dict().get(key)
        if status != 0:
            raise HomeAssistantError(
                f"Device did not confirm {key}={self._value} (reply: {reply.as_dict()})"
            )
        _LOGGER.warning("Battery Recovery command sent: %s=%s", key, self._value)
        await self._coordinator.async_request_refresh()
