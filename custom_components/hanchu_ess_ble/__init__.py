"""The Hanchu ESS BLE integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import device_registry as dr

from .clock_sync import async_sync_clock
from .const import DOMAIN, PLATFORMS
from .coordinator import HanchuBleCoordinator
from .models import battery_model_from_values, model_from_values
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

    @callback
    def _async_sync_device_model() -> None:
        """Write the inverter model (P003) to the device registry once known.

        Home Assistant only reads an entity's device_info when the entity is
        first added. P003 may not have been read by then (it can arrive a
        cycle or two after startup), which would leave the device card on
        the generic model name, so update the registry entry directly.
        """
        values = coordinator.data.values if coordinator.data else None
        model = model_from_values(values)
        if model is None:
            return
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, coordinator.address)}
        )
        if device is not None and device.model != model.name:
            device_registry.async_update_device(device.id, model=model.name)
            _LOGGER.debug("Set device model to %s", model.name)

    entry.async_on_unload(coordinator.async_add_listener(_async_sync_device_model))
    _async_sync_device_model()

    def _make_battery_model_sync(battery_coordinator):
        @callback
        def _async_sync_battery_model() -> None:
            """Same as above for a battery pack, from its cell layout (B147)."""
            values = battery_coordinator.data.values if battery_coordinator.data else None
            name = battery_model_from_values(values)
            if name is None:
                return
            device_registry = dr.async_get(hass)
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, battery_coordinator.address)}
            )
            if device is not None and device.model != name:
                device_registry.async_update_device(device.id, model=name)

        return _async_sync_battery_model

    for battery_coordinator in coordinator.battery_coordinators:
        sync = _make_battery_model_sync(battery_coordinator)
        entry.async_on_unload(battery_coordinator.async_add_listener(sync))
        sync()
    # No update listener: the options flow (OptionsFlowWithReload) and the
    # reconfigure flow (async_update_reload_and_abort) both reload the entry
    # themselves. Registering a listener as well reloaded it twice and is
    # reported by Home Assistant, becoming an error from 2026.12.

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

    async def handle_read_registers(call: ServiceCall) -> ServiceResponse:
        """Read arbitrary registers once, without creating any entities.

        Read-only diagnostic for probing registers that aren't in the
        integration yet (e.g. L108/L114/P511 on the inverter, B183 on a
        battery). Reuses the existing coordinator's BLE client, so it waits
        on the same connection lock as normal polling and never overlaps it.
        Keys are requested in their own transaction, separate from the
        normal poll batch, so an unsupported key can't break regular polling.
        """
        raw_keys = call.data.get("keys", [])
        if isinstance(raw_keys, str):
            raw_keys = raw_keys.split(",")
        keys = [str(k).strip().upper() for k in raw_keys if str(k).strip()]
        if not keys:
            return {"success": False, "message": "No register keys given"}
        battery_address = call.data.get("battery_address")

        inverter: HanchuBleCoordinator | None = None
        for value in hass.data.get(DOMAIN, {}).values():
            if isinstance(value, HanchuBleCoordinator):
                inverter = value
                break
        if inverter is None:
            return {"success": False, "message": "No Hanchu ESS BLE device configured"}

        if battery_address:
            target = next(
                (
                    bc
                    for bc in inverter.battery_coordinators
                    if bc.address.upper() == str(battery_address).upper()
                ),
                None,
            )
            if target is None:
                return {
                    "success": False,
                    "message": f"No configured battery with address {battery_address}",
                }
            client = target.client
            device = f"battery {target.address}"
        else:
            client = inverter.client
            device = f"inverter {inverter.address}"

        try:
            reply = await client.async_read_values(keys, encrypted=True)
        except Exception as err:  # noqa: BLE001 — report any failure back
            return {"success": False, "device": device, "keys": keys, "message": repr(err)}

        values = reply.as_dict()
        return {
            "success": True,
            "device": device,
            "code": reply.code,
            "values": values,
            "missing": [k for k in keys if k not in values],
        }

    if not hass.services.has_service(DOMAIN, "read_registers"):
        hass.services.async_register(
            DOMAIN,
            "read_registers",
            handle_read_registers,
            supports_response=SupportsResponse.ONLY,
        )

    async def handle_sync_clock(call: ServiceCall) -> ServiceResponse:
        """EXPERIMENTAL: check and, if needed, correct the inverter clock.

        Same logic as the Sync Clock button, but returns the before/after
        values so a tester can see exactly what happened. dry_run reads and
        reports without writing anything.
        """
        coordinator: HanchuBleCoordinator | None = None
        entry_id: str | None = None
        for key, value in hass.data.get(DOMAIN, {}).items():
            if isinstance(value, HanchuBleCoordinator):
                coordinator, entry_id = value, key
                break
        if coordinator is None:
            return {"success": False, "message": "No Hanchu ESS BLE device configured"}

        try:
            result = await async_sync_clock(
                coordinator,
                hass.data[DOMAIN].get(f"{entry_id}_pending_writes"),
                force=bool(call.data.get("force", False)),
                dry_run=bool(call.data.get("dry_run", True)),
            )
        except Exception as err:  # noqa: BLE001 — report any failure back
            return {"success": False, "message": str(err) or repr(err)}
        return {"success": True, **result}

    if not hass.services.has_service(DOMAIN, "sync_clock"):
        hass.services.async_register(
            DOMAIN,
            "sync_clock",
            handle_sync_clock,
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
