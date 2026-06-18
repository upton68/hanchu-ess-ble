"""Number platform for Hanchu ESS BLE."""
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

POWER_LIMIT_KEYS = {"charge_power_limit", "discharge_power_limit"}

NUMBERS = {
    "charge_power_limit": {
        "name": "Charge Power Limit",
        "key": "L017",
        "unit": UnitOfPower.WATT,
        "icon": "mdi:battery-charging",
        "step": 100,
        "min": 0,
        "max": 5000,
        "dynamic_max": "P005",
    },
    "discharge_power_limit": {
        "name": "Discharge Power Limit",
        "key": "L018",
        "unit": UnitOfPower.WATT,
        "icon": "mdi:battery-arrow-down",
        "step": 100,
        "min": 0,
        "max": 5000,
        "dynamic_max": "P005",
    },
    "max_charge_soc": {
        "name": "Maximum Charge SOC",
        "key": "P647",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-high",
        "step": 1,
        "min": 50,
        "max": 100,
    },
    "min_discharge_soc": {
        "name": "Minimum Discharge SOC",
        "key": "P648",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-low",
        "step": 1,
        "min": 5,
        "max": 45,
    },
    "grid_charge_soc_limit": {
        "name": "Grid to Battery Charge Maximum",
        "key": "L074",
        "unit": PERCENTAGE,
        "icon": "mdi:transmission-tower",
        "step": 1,
        "min": 20,
        "max": 100,
    },
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HanchuBleNumber(coordinator, entry, number_key, config)
        for number_key, config in NUMBERS.items()
    ]
    async_add_entities(entities)


class HanchuBleNumber(CoordinatorEntity, NumberEntity):
    """Represents a numeric control for Hanchu ESS BLE."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry, number_key, config):
        super().__init__(coordinator)
        self._entry = entry
        self._config = config
        self._attr_name = config["name"]
        self._attr_unique_id = f"{coordinator.address}_{number_key}"
        self._attr_icon = config["icon"]
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_native_step = config["step"]
        self._attr_native_min_value = config["min"]
        self._attr_native_max_value = config["max"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.configured_name,
            manufacturer="Hanchu",
            model="ESS Device (Local BLE)",
        )

    @property
    def native_max_value(self) -> float:
        """Return max value — use P005 dynamically for power limits."""
        dynamic_key = self._config.get("dynamic_max")
        if dynamic_key and self.coordinator.data and self.coordinator.data.values:
            raw = self.coordinator.data.values.get(dynamic_key)
            if raw is not None:
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    pass
        return self._config["max"]

    @property
    def native_value(self) -> float | None:
        """Return the current value, derived live from coordinator data."""
        if not self.coordinator.data or not self.coordinator.data.values:
            return None
        value = self.coordinator.data.values.get(self._config["key"])
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to the device over BLE."""
        int_value = int(value)
        try:
            reply = await self.coordinator.client.async_write_value(
                self._config["key"], int_value, encrypted=True
            )
        except Exception as err:
            _LOGGER.error("Failed to set %s: %s", self._config["name"], err)
            return

        result = reply.as_dict().get(self._config["key"])
        if result == 0:
            _LOGGER.info("%s set to %s", self._config["name"], int_value)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "%s write did not confirm success: %s", self._config["name"], reply.as_dict()
            )
