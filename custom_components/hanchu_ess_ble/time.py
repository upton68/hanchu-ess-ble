"""Time platform for Hanchu ESS BLE - Charge and discharge time slot controls."""
import asyncio
import logging
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2

TIME_SLOTS = {
    "charge_slot_1_start": {"name": "Charge Slot 1 Start", "key": "L005", "icon": "mdi:battery-clock"},
    "charge_slot_1_end": {"name": "Charge Slot 1 End", "key": "L006", "icon": "mdi:battery-clock"},
    "charge_slot_2_start": {"name": "Charge Slot 2 Start", "key": "L007", "icon": "mdi:battery-clock"},
    "charge_slot_2_end": {"name": "Charge Slot 2 End", "key": "L008", "icon": "mdi:battery-clock"},
    "charge_slot_3_start": {"name": "Charge Slot 3 Start", "key": "L009", "icon": "mdi:battery-clock"},
    "charge_slot_3_end": {"name": "Charge Slot 3 End", "key": "L010", "icon": "mdi:battery-clock"},
    "discharge_slot_1_start": {"name": "Discharge Slot 1 Start", "key": "L011", "icon": "mdi:battery-clock-outline"},
    "discharge_slot_1_end": {"name": "Discharge Slot 1 End", "key": "L012", "icon": "mdi:battery-clock-outline"},
    "discharge_slot_2_start": {"name": "Discharge Slot 2 Start", "key": "L013", "icon": "mdi:battery-clock-outline"},
    "discharge_slot_2_end": {"name": "Discharge Slot 2 End", "key": "L014", "icon": "mdi:battery-clock-outline"},
    "discharge_slot_3_start": {"name": "Discharge Slot 3 Start", "key": "L015", "icon": "mdi:battery-clock-outline"},
    "discharge_slot_3_end": {"name": "Discharge Slot 3 End", "key": "L016", "icon": "mdi:battery-clock-outline"},
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HanchuBleTimeSlot(coordinator, entry, slot_key, config)
        for slot_key, config in TIME_SLOTS.items()
    ]
    async_add_entities(entities)


class HanchuBleTimeSlot(CoordinatorEntity, TimeEntity):
    """Represents a charge or discharge time slot for Hanchu ESS BLE."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, slot_key, config):
        super().__init__(coordinator)
        self._entry = entry
        self._config = config
        self._attr_name = config["name"]
        self._attr_unique_id = f"{coordinator.address}_{slot_key}"
        self._attr_icon = config["icon"]
        self._debounce_task = None
        self._pending_value = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.configured_name,
            manufacturer="Hanchu",
            model="ESS Device (Local BLE)",
        )

    @property
    def native_value(self) -> time | None:
        """Return the current slot time, derived live from coordinator data."""
        if self._pending_value is not None:
            return self._pending_value
        if not self.coordinator.data or not self.coordinator.data.values:
            return None
        raw = self.coordinator.data.values.get(self._config["key"])
        if raw is None:
            return None
        try:
            total_seconds = int(float(raw))
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return time(hours, minutes)
        except (ValueError, TypeError):
            return None

    async def async_set_value(self, value: time) -> None:
        """Debounce time slot changes to avoid multiple BLE round trips."""
        self._pending_value = value
        self.async_write_ha_state()

        if self._debounce_task:
            self._debounce_task.cancel()

        self._debounce_task = asyncio.ensure_future(self._send_after_delay())

    async def _send_after_delay(self) -> None:
        """Wait for the debounce period then send to the device."""
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            value = self._pending_value
            if value is None:
                return
            seconds = (value.hour * 3600) + (value.minute * 60)
            try:
                reply = await self.coordinator.client.async_write_value(
                    self._config["key"], seconds, encrypted=True
                )
            except Exception as err:
                _LOGGER.error("Failed to set %s: %s", self._config["name"], err)
                return

            result = reply.as_dict().get(self._config["key"])
            if result == 0:
                _LOGGER.info("%s set to %s seconds", self._config["name"], seconds)
                self._pending_value = None
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    "%s write did not confirm success: %s",
                    self._config["name"],
                    reply.as_dict(),
                )
        except asyncio.CancelledError:
            pass
