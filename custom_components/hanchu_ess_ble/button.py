"""Button entities for the Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
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
