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

async def handle_bench_test_multi_write(call: ServiceCall) -> None:
    client: HanchuBleClient = hass.data[DOMAIN][entry.entry_id]["ble_client"]
    pairs = [
        ("L011", call.data["value_1"]),
        ("L012", call.data["value_2"]),
    ]
    reply = await client.bench_test_multi_write(pairs)
    _LOGGER.warning("Bench test multi-write reply: %s", reply.as_dict())

    # Immediately read back both keys to confirm the device actually
    # applied both, not just the first entry in the array.
    readback = await client.async_read_values([k for k, _ in pairs])
    _LOGGER.warning("Bench test read-back: %s", readback.as_dict())

hass.services.async_register(DOMAIN, "bench_test_multi_write", handle_bench_test_multi_write)
