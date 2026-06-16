"""The Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import HanchuBleCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hanchu ESS BLE from a config entry."""
    coordinator = HanchuBleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_test_write(call):
        key = call.data["key"]
        value = call.data["value"]
        _LOGGER.info("[HANCHU_BLE_TEST] Writing %s = %s", key, value)
        try:
            reply = await coordinator.client.async_write_value(key, value, encrypted=True)
            _LOGGER.info("[HANCHU_BLE_TEST] Write result: %s", reply.as_dict())
        except Exception as err:
            _LOGGER.error("[HANCHU_BLE_TEST] Write failed: %s", err)

    hass.services.async_register(DOMAIN, "test_write", handle_test_write)

    async def handle_test_read(call):
        keys = call.data["keys"]
        _LOGGER.info("[HANCHU_BLE_TEST] Reading %s", keys)
        try:
            reply = await coordinator.client.async_read_values(keys, encrypted=True)
            _LOGGER.info("[HANCHU_BLE_TEST] Read result: %s", reply.as_dict())
        except Exception as err:
            _LOGGER.error("[HANCHU_BLE_TEST] Read failed: %s", err)

    hass.services.async_register(DOMAIN, "test_read", handle_test_read)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: HanchuBleCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
