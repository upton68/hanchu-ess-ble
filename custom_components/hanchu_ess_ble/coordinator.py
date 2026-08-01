"""Coordinator for Hanchu ESS BLE devices."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothChange, BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble_client import HanchuBleClient, HanchuBleSnapshot, snapshot_from_service_info
from .const import (
    BATTERY_POLL_KEYS,
    BATTERY_SCAN_INTERVAL,
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_DEVICE_NAME,
    DEFAULT_NAME,
    FAST_POLL_KEYS,
    MAX_CONSECUTIVE_FAILURES,
    SLOW_POLL_KEYS,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HanchuCoordinatorData:
    """Coordinator state exposed to entities."""

    address: str
    configured_name: str
    discovered_name: str | None = None
    connectable: bool = False
    rssi: int | None = None
    last_seen: datetime | None = None
    is_present: bool = False
    manufacturer_data: dict[int, bytes] | None = None
    service_data: dict[str, bytes] | None = None
    values: dict[str, Any] | None = None
    last_updated: dict[str, datetime] | None = None
    # BLE diagnostic fields
    last_successful_read: datetime | None = None
    consecutive_failures: int = 0
    last_cycle_duration: float | None = None


class HanchuBleCoordinator(DataUpdateCoordinator[HanchuCoordinatorData]):
    """Track a Hanchu inverter by its BLE address."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.configured_name: str = entry.data.get(CONF_DEVICE_NAME, DEFAULT_NAME)
        self.client = HanchuBleClient(hass, self.address, self.configured_name)
        self._unsubscribe_bluetooth: CALLBACK_TYPE | None = None
        self._unsubscribe_unavailable: CALLBACK_TYPE | None = None
        # Tracks which SLOW_POLL_KEYS entry to request this cycle.
        # Advances by one each cycle, wrapping at len(SLOW_POLL_KEYS).
        self._slow_poll_index: int = 0
        # Running failure count — reset on each successful read.
        self._consecutive_failures: int = 0
        # Battery sub-coordinators, one per configured battery address —
        # populated in async_setup(), torn down in async_shutdown().
        self.battery_coordinators: list[HanchuBatteryCoordinator] = []

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.address}",
            update_interval=SCAN_INTERVAL,
        )

    async def async_setup(self) -> None:
        """Register Bluetooth listeners after the first refresh."""
        if self._unsubscribe_bluetooth is not None:
            return

        self._unsubscribe_bluetooth = bluetooth.async_register_callback(
            self.hass,
            self._async_handle_bluetooth_event,
            {"address": self.address},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        self._unsubscribe_unavailable = bluetooth.async_track_unavailable(
            self.hass,
            self._async_handle_unavailable,
            self.address,
            connectable=False,
        )

        # Set up one HanchuBatteryCoordinator per configured battery address.
        battery_addresses = self.entry.options.get(CONF_BATTERY_ADDRESSES, [])
        for battery_address in battery_addresses:
            battery_coordinator = HanchuBatteryCoordinator(
                self.hass, self.entry, battery_address
            )
            await battery_coordinator.async_config_entry_first_refresh()
            await battery_coordinator.async_setup()
            self.battery_coordinators.append(battery_coordinator)

    async def async_shutdown(self) -> None:
        """Release Bluetooth listeners."""
        if self._unsubscribe_bluetooth is not None:
            self._unsubscribe_bluetooth()
            self._unsubscribe_bluetooth = None

        if self._unsubscribe_unavailable is not None:
            self._unsubscribe_unavailable()
            self._unsubscribe_unavailable = None

        for battery_coordinator in self.battery_coordinators:
            await battery_coordinator.async_shutdown()
        self.battery_coordinators.clear()

    async def _async_update_data(self) -> HanchuCoordinatorData:
        """Fetch the latest Bluetooth information for the configured inverter."""
        _LOGGER.debug("Refreshing Hanchu coordinator for address=%s", self.address)
        service_info = bluetooth.async_last_service_info(
            self.hass,
            self.address,
            connectable=False,
        )

        if service_info is None:
            if self.data is not None:
                return HanchuCoordinatorData(
                    address=self.data.address,
                    configured_name=self.data.configured_name,
                    discovered_name=self.data.discovered_name,
                    connectable=self.data.connectable,
                    rssi=self.data.rssi,
                    last_seen=self.data.last_seen,
                    is_present=False,
                    manufacturer_data=self.data.manufacturer_data,
                    service_data=self.data.service_data,
                    values=self.data.values,
                    last_updated=self.data.last_updated,
                    last_successful_read=self.data.last_successful_read,
                    consecutive_failures=self.data.consecutive_failures,
                    last_cycle_duration=self.data.last_cycle_duration,
                )
            raise ConfigEntryNotReady(
                f"No BLE advertisements seen yet for configured address {self.address}"
            )

        # Build the key list for this cycle: all fast keys plus one slow key.
        slow_key = SLOW_POLL_KEYS[self._slow_poll_index]
        poll_keys = list(FAST_POLL_KEYS) + [slow_key]
        _LOGGER.debug(
            "Cycle slow_poll_index=%d polling slow key=%s",
            self._slow_poll_index,
            slow_key,
        )

        snapshot = snapshot_from_service_info(service_info)
        cycle_start = time.monotonic()
        try:
            reply = await self.client.async_read_values(poll_keys, encrypted=True)
        except Exception as err:
            self._consecutive_failures += 1
            cycle_duration = round(time.monotonic() - cycle_start, 2)
            _LOGGER.debug(
                "Failed Hanchu coordinator refresh for address=%s (consecutive failures=%d)",
                self.address,
                self._consecutive_failures,
                exc_info=True,
            )

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                # Too many failures in a row — escalate to a real failure so
                # HA surfaces it and entities correctly reflect a stale/dead
                # state rather than silently persisting old values forever.
                raise UpdateFailed(f"Failed to read inverter values: {err}") from err

            # Below threshold: treat as a transient miss. Carry forward the
            # last known values (via _build_data's merge path, triggered by
            # values=None) instead of failing the whole coordinator update,
            # so entities don't flicker unavailable on every dropped read.
            _LOGGER.warning(
                "Hanchu BLE read failed for address=%s (%d/%d consecutive "
                "failures), retaining last known values: %s",
                self.address,
                self._consecutive_failures,
                MAX_CONSECUTIVE_FAILURES,
                err,
            )
            return self._build_data(
                snapshot,
                is_present=True,
                values=None,
                consecutive_failures=self._consecutive_failures,
                last_cycle_duration=cycle_duration,
            )

        cycle_duration = round(time.monotonic() - cycle_start, 2)

        # Successful read — reset failure counter and advance slow poll index.
        self._consecutive_failures = 0
        self._slow_poll_index = (self._slow_poll_index + 1) % len(SLOW_POLL_KEYS)

        _LOGGER.debug(
            "Hanchu coordinator refresh succeeded for address=%s duration=%.2fs values=%s",
            self.address,
            cycle_duration,
            reply.as_dict(),
        )
        return self._build_data(
            snapshot,
            is_present=True,
            values=reply.as_dict(),
            last_successful_read=datetime.now(timezone.utc),
            consecutive_failures=0,
            last_cycle_duration=cycle_duration,
        )

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Handle live Bluetooth advertisement updates."""
        del change
        snapshot = snapshot_from_service_info(service_info)
        self.async_set_updated_data(self._build_data(snapshot, is_present=True))

    @callback
    def _async_handle_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Mark the device unavailable when advertisements stop."""
        del service_info
        if self.data is None:
            return

        self.async_set_updated_data(
            HanchuCoordinatorData(
                address=self.data.address,
                configured_name=self.data.configured_name,
                discovered_name=self.data.discovered_name,
                connectable=self.data.connectable,
                rssi=self.data.rssi,
                last_seen=self.data.last_seen,
                is_present=False,
                manufacturer_data=self.data.manufacturer_data,
                service_data=self.data.service_data,
                values=self.data.values,
                last_updated=self.data.last_updated,
                last_successful_read=self.data.last_successful_read,
                consecutive_failures=self.data.consecutive_failures,
                last_cycle_duration=self.data.last_cycle_duration,
            )
        )

    def _build_data(
        self,
        snapshot: HanchuBleSnapshot,
        *,
        is_present: bool,
        values: dict[str, Any] | None = None,
        last_successful_read: datetime | None = None,
        consecutive_failures: int | None = None,
        last_cycle_duration: float | None = None,
    ) -> HanchuCoordinatorData:
        """Convert BLE data into coordinator state with merged values and timestamps.

        Rather than replacing the full values dict each cycle, new values are
        merged into the previous state. This means registers not polled this
        cycle (i.e. slow-tier keys) retain their last good value rather than
        becoming None or unavailable. Timestamps are only updated for keys
        that actually returned a value this cycle.
        """
        # Merge register values into previous state so unpolled keys persist.
        if values is not None:
            merged_values = dict(self.data.values) if self.data and self.data.values else {}
            merged_values.update(values)
        else:
            merged_values = self.data.values if self.data else None

        # Update timestamps only for keys returned this cycle.
        now = datetime.now(timezone.utc)
        if values is not None:
            merged_ts: dict[str, datetime] = (
                dict(self.data.last_updated) if self.data and self.data.last_updated else {}
            )
            for key in values:
                merged_ts[key] = now
        else:
            merged_ts = self.data.last_updated if self.data else None

        return HanchuCoordinatorData(
            address=snapshot.address,
            configured_name=self.configured_name,
            discovered_name=snapshot.name,
            connectable=snapshot.connectable,
            rssi=snapshot.rssi,
            last_seen=snapshot.last_seen,
            is_present=is_present,
            manufacturer_data=snapshot.manufacturer_data,
            service_data=snapshot.service_data,
            values=merged_values,
            last_updated=merged_ts,
            last_successful_read=(
                last_successful_read
                if last_successful_read is not None
                else (self.data.last_successful_read if self.data else None)
            ),
            consecutive_failures=(
                consecutive_failures
                if consecutive_failures is not None
                else (self.data.consecutive_failures if self.data else 0)
            ),
            last_cycle_duration=(
                last_cycle_duration
                if last_cycle_duration is not None
                else (self.data.last_cycle_duration if self.data else None)
            ),
        )


class HanchuBatteryCoordinator(DataUpdateCoordinator[HanchuCoordinatorData]):
    """Track an individual Hanchu battery pack by its own BLE address.

    Reuses HanchuCoordinatorData since the shape (address, values,
    last_updated, diagnostic fields) is identical to the inverter's — no
    need for a separate dataclass. Polls on a much slower interval than the
    inverter (BATTERY_SCAN_INTERVAL, default 300s) since battery SOC/voltage/
    temperature change slowly, and reads all BATTERY_POLL_KEYS in a single
    connection each cycle rather than tiering fast/slow like the inverter —
    ten keys in one request has already been verified working against real
    hardware.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
    ) -> None:
        """Initialise the battery coordinator."""
        self.hass = hass
        self.entry = entry
        self.address = address
        # Configured name isn't user-editable per battery (yet) — derived
        # from the address so entities/devices are still distinguishable.
        self.configured_name = f"Hanchu Battery {address[-5:].replace(':', '')}"
        self.client = HanchuBleClient(hass, address, self.configured_name)
        self._unsubscribe_bluetooth: CALLBACK_TYPE | None = None
        self._unsubscribe_unavailable: CALLBACK_TYPE | None = None
        self._consecutive_failures: int = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_battery_{address}",
            update_interval=BATTERY_SCAN_INTERVAL,
        )

    async def async_setup(self) -> None:
        """Register Bluetooth listeners after the first refresh."""
        if self._unsubscribe_bluetooth is not None:
            return

        self._unsubscribe_bluetooth = bluetooth.async_register_callback(
            self.hass,
            self._async_handle_bluetooth_event,
            {"address": self.address},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        self._unsubscribe_unavailable = bluetooth.async_track_unavailable(
            self.hass,
            self._async_handle_unavailable,
            self.address,
            connectable=False,
        )

    async def async_shutdown(self) -> None:
        """Release Bluetooth listeners."""
        if self._unsubscribe_bluetooth is not None:
            self._unsubscribe_bluetooth()
            self._unsubscribe_bluetooth = None

        if self._unsubscribe_unavailable is not None:
            self._unsubscribe_unavailable()
            self._unsubscribe_unavailable = None

    async def _async_update_data(self) -> HanchuCoordinatorData:
        """Fetch the latest values for this battery pack."""
        _LOGGER.debug("Refreshing Hanchu battery coordinator for address=%s", self.address)
        service_info = bluetooth.async_last_service_info(
            self.hass,
            self.address,
            connectable=False,
        )

        if service_info is None:
            if self.data is not None:
                return self._build_data(
                    snapshot=None,
                    is_present=False,
                    values=None,
                )
            raise ConfigEntryNotReady(
                f"No BLE advertisements seen yet for battery address {self.address}"
            )

        snapshot = snapshot_from_service_info(service_info)
        cycle_start = time.monotonic()
        try:
            reply = await self.client.async_read_values(BATTERY_POLL_KEYS, encrypted=True)
        except Exception as err:
            self._consecutive_failures += 1
            cycle_duration = round(time.monotonic() - cycle_start, 2)
            _LOGGER.debug(
                "Failed Hanchu battery refresh for address=%s (consecutive failures=%d)",
                self.address,
                self._consecutive_failures,
                exc_info=True,
            )

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise UpdateFailed(f"Failed to read battery values: {err}") from err

            _LOGGER.warning(
                "Hanchu battery BLE read failed for address=%s (%d/%d consecutive "
                "failures), retaining last known values: %s",
                self.address,
                self._consecutive_failures,
                MAX_CONSECUTIVE_FAILURES,
                err,
            )
            return self._build_data(
                snapshot,
                is_present=True,
                values=None,
                consecutive_failures=self._consecutive_failures,
                last_cycle_duration=cycle_duration,
            )

        cycle_duration = round(time.monotonic() - cycle_start, 2)
        self._consecutive_failures = 0

        _LOGGER.debug(
            "Hanchu battery refresh succeeded for address=%s duration=%.2fs values=%s",
            self.address,
            cycle_duration,
            reply.as_dict(),
        )
        return self._build_data(
            snapshot,
            is_present=True,
            values=reply.as_dict(),
            last_successful_read=datetime.now(timezone.utc),
            consecutive_failures=0,
            last_cycle_duration=cycle_duration,
        )

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Handle live Bluetooth advertisement updates (RSSI/presence only)."""
        del change
        snapshot = snapshot_from_service_info(service_info)
        self.async_set_updated_data(self._build_data(snapshot, is_present=True, values=None))

    @callback
    def _async_handle_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Mark the battery unavailable when advertisements stop."""
        del service_info
        if self.data is None:
            return
        self.async_set_updated_data(self._build_data(None, is_present=False, values=None))

    def _build_data(
        self,
        snapshot: HanchuBleSnapshot | None,
        *,
        is_present: bool,
        values: dict[str, Any] | None,
        last_successful_read: datetime | None = None,
        consecutive_failures: int | None = None,
        last_cycle_duration: float | None = None,
    ) -> HanchuCoordinatorData:
        """Merge new values into previous state, same pattern as the inverter."""
        if values is not None:
            merged_values = dict(self.data.values) if self.data and self.data.values else {}
            merged_values.update(values)
        else:
            merged_values = self.data.values if self.data else None

        now = datetime.now(timezone.utc)
        if values is not None:
            merged_ts: dict[str, datetime] = (
                dict(self.data.last_updated) if self.data and self.data.last_updated else {}
            )
            for key in values:
                merged_ts[key] = now
        else:
            merged_ts = self.data.last_updated if self.data else None

        return HanchuCoordinatorData(
            address=snapshot.address if snapshot else self.address,
            configured_name=self.configured_name,
            discovered_name=(
                snapshot.name if snapshot else (self.data.discovered_name if self.data else None)
            ),
            connectable=(
                snapshot.connectable if snapshot else (self.data.connectable if self.data else False)
            ),
            rssi=snapshot.rssi if snapshot else (self.data.rssi if self.data else None),
            last_seen=snapshot.last_seen if snapshot else (self.data.last_seen if self.data else None),
            is_present=is_present,
            manufacturer_data=snapshot.manufacturer_data if snapshot else None,
            service_data=snapshot.service_data if snapshot else None,
            values=merged_values,
            last_updated=merged_ts,
            last_successful_read=(
                last_successful_read
                if last_successful_read is not None
                else (self.data.last_successful_read if self.data else None)
            ),
            consecutive_failures=(
                consecutive_failures
                if consecutive_failures is not None
                else (self.data.consecutive_failures if self.data else 0)
            ),
            last_cycle_duration=(
                last_cycle_duration
                if last_cycle_duration is not None
                else (self.data.last_cycle_duration if self.data else None)
            ),
        )
