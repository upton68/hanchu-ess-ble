"""Select platform for Hanchu ESS BLE - Work Mode control."""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

WORK_MODES = {
    "Self-consumption": "1",
    "Backup Energy": "2",
    "User-defined": "3",
    "Off-grid": "4",
}

WORK_MODES_REVERSE = {v: k for k, v in WORK_MODES.items()}

WORK_MODE_KEY = "P651"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WorkModeSelect(coordinator, entry)])


class WorkModeSelect(SelectEntity):
    """Work mode selector for Hanchu ESS BLE."""

    _attr_has_entity_name = True
    _attr_name = "Work Mode"
    _attr_icon = "mdi:dip-switch"
    _attr_options = list(WORK_MODES.keys())

    def __init__(self, coordinator, entry):
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_work_mode"
        self._attr_current_option = None

        # Set initial value from the coordinator's last poll, if available
        if coordinator.data and coordinator.data.values:
            value = coordinator.data.values.get(WORK_MODE_KEY)
            if value is not None:
                mode = WORK_MODES_REVERSE.get(str(int(float(value))))
                if mode:
                    self._attr_current_option = mode

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.address)},
            name=self._coordinator.configured_name,
            manufacturer="Hanchu",
            model="ESS Device (Local BLE)",
        )

    async def async_select_option(self, option: str) -> None:
        """Send work mode change to device over BLE."""
        value = WORK_MODES.get(option)
        if value is None:
            _LOGGER.error("Unknown work mode: %s", option)
            return
        try:
            reply = await self._coordinator.client.async_write_value(
                WORK_MODE_KEY, value, encrypted=True
            )
        except Exception as err:
            _LOGGER.error("Failed to set work mode: %s", err)
            return

        result = reply.as_dict().get(WORK_MODE_KEY)
        if result == 0:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.info("Work mode set to %s", option)
        else:
            _LOGGER.error("Work mode write did not confirm success: %s", reply.as_dict())
