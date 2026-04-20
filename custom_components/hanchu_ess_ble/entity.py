"""Shared entity helpers for Hanchu ESS BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HanchuBleCoordinator


class HanchuCoordinatorEntity(CoordinatorEntity[HanchuBleCoordinator]):
    """Base entity backed by the Hanchu BLE coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HanchuBleCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the physical inverter device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=self.coordinator.data.configured_name,
        )

    @property
    def available(self) -> bool:
        """Report availability based on recent BLE activity."""
        return self.coordinator.last_update_success and self.coordinator.data.is_present
