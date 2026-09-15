"""The Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import HanchuBleCoordinator
from .pending_writes import PendingWriteBuffer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hanchu ESS BLE from a config entry."""
    coordinator = HanchuBleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # One PendingWriteBuffer per device, shared by the number/select entities
    # that stage edits and the confirm/discard buttons that flush or clear
    # them. Stored under a suffixed key alongside the coordinator rather than
    # restructuring hass.data[DOMAIN][entry.entry_id] into a wrapper object —
    # smallest change for now; worth tidying into a proper structure later
    # as a deliberate refactor rather than bundling it into this change.
    pending_writes = PendingWriteBuffer(hass, coordinator.client)
    hass.data[DOMAIN][entry.entry_id + "_pending_writes"] = pending_writes

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: HanchuBleCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id + "_pending_writes", None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
