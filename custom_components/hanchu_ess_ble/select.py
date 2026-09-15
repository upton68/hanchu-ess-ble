"""Select platform for Hanchu ESS BLE - Work Mode control."""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .pending_writes import PendingWriteBuffer

_LOGGER = logging.getLogger(__name__)

WORK_MODES = {
    "Self-consumption": 1,
    "Backup Energy": 2,
    "User-defined": 3,
    "Off-grid": 4,
}

WORK_MODES_REVERSE = {v: k for k, v in WORK_MODES.items()}

WORK_MODE_KEY = "P651"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    pending_writes = hass.data[DOMAIN][entry.entry_id + "_pending_writes"]
    async_add_entities([WorkModeSelect(coordinator, pending_writes, entry)])


class WorkModeSelect(CoordinatorEntity, SelectEntity):
    """Work mode selector for Hanchu ESS BLE.

    Edits are staged into the shared PendingWriteBuffer rather than written
    to BLE immediately — nothing reaches the device until the Confirm Write
    button is pressed, at which point this key is flushed together with
    whatever else is staged at that moment, in one BLE connection.
    """

    _attr_has_entity_name = True
    _attr_name = "Work Mode"
    _attr_icon = "mdi:dip-switch"
    _attr_options = list(WORK_MODES.keys())

    def __init__(self, coordinator, pending_writes: PendingWriteBuffer, entry):
        super().__init__(coordinator)
        self._pending_writes = pending_writes
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_work_mode"
        self._pending_value: str | None = None
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
    def current_option(self) -> str | None:
        """Return the current work mode, derived live from coordinator data."""
        if self._pending_value is not None:
            return self._pending_value
        if not self.coordinator.data or not self.coordinator.data.values:
            return None
        value = self.coordinator.data.values.get(WORK_MODE_KEY)
        if value is None:
            return None
        try:
            return WORK_MODES_REVERSE.get(int(float(value)))
        except (ValueError, TypeError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Stage the new work mode in the shared buffer; nothing is written yet.

        The value shows immediately (optimistic UI) via _pending_value, but
        only reaches the device once Confirm Write is pressed. Discard
        Changes, or the buffer's own auto-discard timeout, will clear it
        instead — see _handle_pending_change.
        """
        value = WORK_MODES.get(option)
        if value is None:
            _LOGGER.error("Unknown work mode: %s", option)
            return
        self._pending_value = option
        self._pending_writes.stage(WORK_MODE_KEY, value)
        self.async_write_ha_state()

    def _handle_pending_change(self) -> None:
        """React to the buffer changing (staged, confirmed, or discarded).

        Fires for every key's stage/confirm/discard, not just this entity's
        own — so only act when THIS entity's key has actually transitioned
        from pending to not-pending, rather than on every unrelated change.
        """
        if self._pending_value is not None and not self._pending_writes.is_pending(WORK_MODE_KEY):
            self._pending_value = None
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        self.async_write_ha_state()
