"""BLE-facing helpers for the Hanchu ESS integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak


@dataclass(slots=True)
class HanchuBleSnapshot:
    """State derived from the latest BLE advertisement."""

    address: str
    name: str | None
    rssi: int | None
    connectable: bool
    last_seen: datetime
    manufacturer_data: dict[int, bytes]
    service_data: dict[str, bytes]


def snapshot_from_service_info(
    service_info: BluetoothServiceInfoBleak,
) -> HanchuBleSnapshot:
    """Convert Home Assistant Bluetooth data into our internal snapshot."""

    return HanchuBleSnapshot(
        address=service_info.address,
        name=service_info.name,
        rssi=service_info.rssi,
        connectable=service_info.connectable,
        last_seen=service_info.time,
        manufacturer_data=dict(service_info.manufacturer_data),
        service_data=dict(service_info.service_data),
    )
