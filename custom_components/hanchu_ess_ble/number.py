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
from .pending_writes import PendingWriteBuffer

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
    pending_writes = hass.data[DOMAIN][entry.entry_id + "_pending_writes"]
    entities = [
        HanchuBleNumber(coordinator, pending_writes, entry, number_key, config)
        for number_key, config in NUMBERS.items()
    ]
    async_add_entities(entities)


class HanchuBleNumber(CoordinatorEntity, NumberEntity):
    """Represents a numeric control for Hanchu ESS BLE.

    Edits are staged into the shared PendingWriteBuffer rather than written
    to BLE immediately — nothing reaches the device until the Confirm Write
    button is pressed, at which point this key is flushed together with
    whatever else is staged at that moment, in one BLE connection.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, pending_writes: PendingWriteBuffer, entry, number_key, config):
        super().__init__(coordinator)
        self._pending_writes = pending_writes
        self._entry = entry
        self._config = config
        self._attr_name = config["name"]
        self._attr_unique_id = f"{coordinator.address}_{number_key}"
        self._attr_icon = config["icon"]
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_native_step = config["step"]
        self._attr_native_min_value = config["min"]
        self._attr_native_max_value = config["max"]
        self._pending_value: float | None = None
        pending_writes.add_listener(self._handle_pending_change)

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
        if self._pending_value is not None:
            return self._pending_value
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
        """Stage the new value in the shared buffer; nothing is written yet.

        The value shows immediately (optimistic UI) via _pending_value, but
        only reaches the device once Confirm Write is pressed. Discard
        Changes, or the buffer's own auto-discard timeout, will clear it
        instead — see _handle_pending_change.
        """
        int_value = int(value)
        self._pending_value = value
        self._pending_writes.stage(self._config["key"], int_value)
        self.async_write_ha_state()

    def _handle_pending_change(self) -> None:
        """React to the buffer changing (staged, confirmed, or discarded).

        Fires for every key's stage/confirm/discard, not just this entity's
        own — so only act when THIS entity's key has actually transitioned
        from pending to not-pending, rather than on every unrelated change.
        """
        key = self._config["key"]
        if self._pending_value is not None and not self._pending_writes.is_pending(key):
            self._pending_value = None
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        self.async_write_ha_state()
