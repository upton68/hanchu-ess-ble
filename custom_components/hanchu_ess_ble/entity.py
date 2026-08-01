"""Shared entity helpers for Hanchu ESS BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, MODEL_BATTERY
from .coordinator import HanchuBleCoordinator, HanchuBatteryCoordinator

class HanchuBatteryCoordinatorEntity(CoordinatorEntity[HanchuBatteryCoordinator]):
    """Base entity backed by a Hanchu battery coordinator.

    Deliberately separate from HanchuCoordinatorEntity (rather than reusing
    it) so each battery registers as its own distinct device in HA, not
    grouped under the inverter's device card.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HanchuBatteryCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the physical battery pack as its own device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL_BATTERY,
            name=self.coordinator.data.configured_name,
        )

    @property
    def available(self) -> bool:
        """Report availability based on recent BLE activity."""
        return self.coordinator.last_update_success and self.coordinator.data.is_present


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
