"""Diagnostics support for Hanchu ESS BLE.

Gives bug reports the BLE health picture (presence, RSSI, failure count,
cycle time, last good read) plus the latest register values for the
inverter and each configured battery, with identifying details removed.

Redacted: Bluetooth addresses, logger names (e.g. "HC:L110..." contains the
logger's serial), and serial-number registers. Batteries are listed as
battery_1, battery_2, ... rather than by address.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import time
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_BATTERY_ADDRESSES, CONF_DEVICE_NAME, DOMAIN
from .coordinator import HanchuBleCoordinator, HanchuCoordinatorData

# Config entry fields and coordinator fields that identify the hardware.
TO_REDACT = {
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_BATTERY_ADDRESSES,
    "address",
    "configured_name",
    "discovered_name",
    "unique_id",
    "title",
}

# Registers whose values identify the hardware.
SERIAL_REGISTERS = {
    "P002",  # Inverter Serial Number
    "B002",  # Battery Serial Number
}


def _iso(value: Any) -> str | None:
    """Format a timestamp, tolerating anything that isn't a datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _seconds_ago(monotonic_value: Any) -> float | None:
    """Age of a Bluetooth advertisement timestamp.

    Home Assistant's Bluetooth stack records advertisement times
    (BluetoothServiceInfoBleak.time) as monotonic clock readings, not
    datetimes, so report how long ago that was instead.
    """
    if not isinstance(monotonic_value, (int, float)):
        return None
    return round(time.monotonic() - monotonic_value, 1)


def _ble_stats(coordinator: Any) -> dict[str, Any] | None:
    """Link drops, timeouts and retries counted since startup."""
    stats = getattr(getattr(coordinator, "client", None), "stats", None)
    return asdict(stats) if stats is not None else None


def _coordinator_state(data: HanchuCoordinatorData | None) -> dict[str, Any] | None:
    """Return a redacted snapshot of one coordinator's data."""
    if data is None:
        return None
    values = {
        key: (REDACTED if key in SERIAL_REGISTERS and value is not None else value)
        for key, value in sorted((data.values or {}).items())
    }
    return {
        "is_present": data.is_present,
        "connectable": data.connectable,
        "rssi": data.rssi,
        "last_advertisement_seconds_ago": _seconds_ago(data.last_seen),
        "last_successful_read": _iso(data.last_successful_read),
        "consecutive_failures": data.consecutive_failures,
        "last_cycle_duration": data.last_cycle_duration,
        "values": values,
        "value_ages": {
            key: _iso(when) for key, when in sorted((data.last_updated or {}).items())
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_dict = entry.as_dict()
    # Bluetooth discovery keys are the device's Bluetooth address, wrapped in
    # DiscoveryKey objects that key-based redaction can't reach. Just say
    # which discovery sources there were.
    entry_dict["discovery_keys"] = sorted(entry_dict.get("discovery_keys") or {})
    diag: dict[str, Any] = {
        "entry": async_redact_data(entry_dict, TO_REDACT),
        "battery_count": len(entry.options.get(CONF_BATTERY_ADDRESSES, [])),
    }

    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get(entry.entry_id)
    if not isinstance(coordinator, HanchuBleCoordinator):
        # Entry not set up (e.g. inverter not advertising yet) — still return
        # what we have so the download never fails.
        diag["state"] = "not_loaded"
        return diag

    diag["inverter"] = {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "ble_stats": _ble_stats(coordinator),
        **(_coordinator_state(coordinator.data) or {}),
    }
    diag["batteries"] = {
        f"battery_{index}": {
            "last_update_success": battery.last_update_success,
            "last_exception": repr(battery.last_exception)
            if battery.last_exception
            else None,
            "ble_stats": _ble_stats(battery),
            **(_coordinator_state(battery.data) or {}),
        }
        for index, battery in enumerate(coordinator.battery_coordinators, start=1)
    }

    pending = domain_data.get(f"{entry.entry_id}_pending_writes")
    if pending is not None:
        diag["pending_writes"] = {
            "count": pending.pending_count,
            "keys": sorted(pending._pending),  # noqa: SLF001 — read-only snapshot
            "is_confirming": pending.is_confirming,
        }

    return diag
