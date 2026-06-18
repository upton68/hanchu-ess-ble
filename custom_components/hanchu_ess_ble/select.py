"""Select platform for Hanchu ESS BLE - Work Mode control."""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

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
    async_add_entities([WorkModeSelect(coordinator, entry)])


class WorkModeSelect(CoordinatorEntity, SelectEntity):
    """Work mode selector for Hanchu ESS BLE."""

    _attr_has_entity_name = True
    _attr_name = "Work Mode"
    _attr_icon = "mdi:dip-switch"
    _attr_options = list(WORK_MODES.keys())

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_work_mode"

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
        """Send work mode change to device over BLE."""
        value = WORK_MODES.get(option)
        if value is None:
            _LOGGER.error("Unknown work mode: %s", option)
            return
        try:
            reply = await self.coordinator.client.async_write_value(
                WORK_MODE_KEY, value, encrypted=True
            )
        except Exception as err:
            _LOGGER.error("Failed to set work mode: %s", err)
            return

        result = reply.as_dict().get(WORK_MODE_KEY)
        if result == 0:
            _LOGGER.info("Work mode set to %s", option)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Work mode write did not confirm success: %s", reply.as_dict())
