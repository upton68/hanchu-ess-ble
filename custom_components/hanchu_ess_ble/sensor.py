"""Sensor entities for the Hanchu ESS BLE integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HanchuBleCoordinator
from .entity import HanchuCoordinatorEntity

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        icon="mdi:bluetooth",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hanchu sensors from a config entry."""
    coordinator: HanchuBleCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HanchuDiagnosticSensor(coordinator, description) for description in SENSORS)


class HanchuDiagnosticSensor(HanchuCoordinatorEntity, SensorEntity):
    """Diagnostic BLE sensor."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: HanchuBleCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self):
        """Return the sensor's value."""
        if self.entity_description.key == "rssi":
            return self.coordinator.data.rssi

        return None
