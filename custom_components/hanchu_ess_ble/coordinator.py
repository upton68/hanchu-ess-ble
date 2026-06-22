"""Coordinator for Hanchu ESS BLE devices."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothChange, BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble_client import HanchuBleClient, HanchuBleSnapshot, snapshot_from_service_info
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    DEFAULT_NAME,
    DEFAULT_POLL_KEYS,
    REGISTER_INTERLACE_SLOTS,
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


class HanchuBleCoordinator(DataUpdateCoordinator[HanchuCoordinatorData]):
    """Track a Hanchu inverter by its BLE address."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        self._slot_counter = 0
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.configured_name: str = entry.data.get(CONF_DEVICE_NAME, DEFAULT_NAME)
        #self.poll_keys = list(DEFAULT_POLL_KEYS)
        self.client = HanchuBleClient(hass, self.address, self.configured_name)
        self._unsubscribe_bluetooth: CALLBACK_TYPE | None = None
        self._unsubscribe_unavailable: CALLBACK_TYPE | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.address}",
            update_interval=SCAN_INTERVAL,
        )
        
    def _select_interlaced_registers(self) -> list[str]:
        """Return the registers to poll this cycle using interlaced slots."""
        slot = REGISTER_INTERLACE_SLOTS[self._slot_counter % len(REGISTER_INTERLACE_SLOTS)]
        self._slot_counter += 1

        # Fast registers = DEFAULT_POLL_KEYS minus all interlaced registers
        interlaced_all = {reg for slot in REGISTER_INTERLACE_SLOTS for reg in slot}
        fast_registers = [reg for reg in DEFAULT_POLL_KEYS if reg not in interlaced_all]

        # Final list = fast registers + this cycle's slot
        return fast_registers + slot


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
                )
            raise ConfigEntryNotReady(
                f"No BLE advertisements seen yet for configured address {self.address}"
            )

        snapshot = snapshot_from_service_info(service_info)
        try:
            keys = self._select_interlaced_registers()
            reply = await self.client.async_read_values(keys, encrypted=True)
        except Exception as err:
            _LOGGER.debug(
                "Failed Hanchu coordinator refresh for address=%s",
                self.address,
                exc_info=True,
            )
            raise UpdateFailed(f"Failed to read inverter values: {err}") from err

        _LOGGER.debug(
            "Hanchu coordinator refresh succeeded for address=%s values=%s",
            self.address,
            reply.as_dict(),
        )
        return self._build_data(
            snapshot,
            is_present=True,
            values=reply.as_dict(),
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
            )
        )

    def _build_data(
        self,
        snapshot: HanchuBleSnapshot,
        *,
        is_present: bool,
        values: dict[str, Any] | None = None,
        ) -> HanchuCoordinatorData:
        """Convert BLE data into coordinator state with merged values and timestamps."""

        # --- Merge register values ---
        if values is not None:
            merged_values = dict(self.data.values) if self.data and self.data.values else {}
            merged_values.update(values)
        else:
            merged_values = self.data.values if self.data else None

        # --- Merge timestamps ---
        now = datetime.utcnow()
        if values is not None:
            merged_ts = dict(self.data.last_updated) if self.data and self.data.last_updated else {}
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
        )