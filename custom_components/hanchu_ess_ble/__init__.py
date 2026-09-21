"""The Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse

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

    # One PendingWriteBuffer per device, shared by the number/select/time
    # entities that stage edits and the confirm/discard buttons that flush
    # or clear them. Stored under a suffixed key alongside the coordinator
    # rather than restructuring hass.data[DOMAIN][entry.entry_id] into a
    # wrapper object — smallest change for now.
    pending_writes = PendingWriteBuffer(hass, coordinator.client)
    hass.data[DOMAIN][entry.entry_id + "_pending_writes"] = pending_writes

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_confirm_write(call: ServiceCall) -> ServiceResponse:
        """Flush staged writes and report success/failure — for automations.

        button.press has no response_variable, so it can't tell a calling
        script whether the write actually succeeded. This service exists
        specifically to give automations (e.g. a Predbat bridge script)
        the same retry-on-failure capability the cloud integration's
        device_control service already provides via its own response.

        Assumes a single configured device, matching every other
        automation-facing helper in this integration so far — if multiple
        Hanchu devices are ever configured at once, this uses whichever
        pending-writes buffer is found first.
        """
        target_buffer: PendingWriteBuffer | None = None
        for key, value in hass.data.get(DOMAIN, {}).items():
            if isinstance(key, str) and key.endswith("_pending_writes"):
                target_buffer = value
                break

        if target_buffer is None:
            return {"success": False, "message": "No Hanchu ESS BLE device configured"}

        if not target_buffer.has_pending:
            return {"success": True, "message": "Nothing staged to confirm"}

        try:
            await target_buffer.confirm()
        except Exception as err:  # noqa: BLE001 — deliberately broad: any
            # failure here (TimeoutError, HanchuProtocolError, or anything
            # else) should be reported back to the calling automation as a
            # failed confirm, not raised and left for the automation to
            # handle as an unexpected exception.
            return {"success": False, "message": str(err)}

        return {"success": True, "message": "Confirmed"}

    if not hass.services.has_service(DOMAIN, "confirm_write"):
        hass.services.async_register(
            DOMAIN,
            "confirm_write",
            handle_confirm_write,
            supports_response=SupportsResponse.OPTIONAL,
        )

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
