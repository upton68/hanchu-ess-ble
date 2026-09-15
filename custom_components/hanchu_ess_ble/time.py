"""Time platform for Hanchu ESS BLE - Charge and discharge time slot controls."""
import logging
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .pending_writes import PendingWriteBuffer

_LOGGER = logging.getLogger(__name__)

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
    pending_writes = hass.data[DOMAIN][entry.entry_id + "_pending_writes"]
    entities = [
        HanchuBleTimeSlot(coordinator, pending_writes, entry, slot_key, config)
        for slot_key, config in TIME_SLOTS.items()
    ]
    async_add_entities(entities)


class HanchuBleTimeSlot(CoordinatorEntity, TimeEntity):
    """Represents a charge or discharge time slot for Hanchu ESS BLE.

    Edits are staged into the shared PendingWriteBuffer rather than written
    to BLE immediately — nothing reaches the device until the Confirm Write
    button is pressed, at which point this key is flushed together with
    whatever else is staged at that moment, in one BLE connection.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, pending_writes: PendingWriteBuffer, entry, slot_key, config):
        super().__init__(coordinator)
        self._pending_writes = pending_writes
        self._entry = entry
        self._config = config
        self._attr_name = config["name"]
        self._attr_unique_id = f"{coordinator.address}_{slot_key}"
        self._attr_icon = config["icon"]
        self._pending_value: time | None = None
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
        """Stage the new time in the shared buffer; nothing is written yet.

        The value shows immediately (optimistic UI) via _pending_value, but
        only reaches the device once Confirm Write is pressed. Discard
        Changes, or the buffer's own auto-discard timeout, will clear it
        instead — see _handle_pending_change.
        """
        self._pending_value = value
        seconds = (value.hour * 3600) + (value.minute * 60)
        self._pending_writes.stage(self._config["key"], seconds)
        self.async_write_ha_state()

    def _handle_pending_change(self) -> None:
        """React to the buffer changing (staged, confirmed, or discarded).

        Fires for every key's stage/confirm/discard, not just this entity's
        own — so only act when THIS entity's key has actually transitioned
        from pending to not-pending (i.e. it was confirmed or discarded),
        rather than on every unrelated buffer change.
        """
        key = self._config["key"]
        if self._pending_value is not None and not self._pending_writes.is_pending(key):
            self._pending_value = None
            # Refresh so the displayed value reflects what the device
            # actually holds now (post-confirm) or already held (post-discard),
            # rather than whatever the coordinator's last poll happened to see.
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        self.async_write_ha_state()
