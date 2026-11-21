"""Set up the whole integration in a test Home Assistant, Bluetooth faked.

The BLE client is replaced at the client level by FakeHanchuClient, which
keeps a register map per device: reads return it, writes update it (unless
told otherwise), and every write is recorded. Everything above the client
(coordinators, PendingWriteBuffer, entities, services) is the real code.

The wire protocol itself is covered separately in test_protocol.py and
test_ble_client.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hanchu_ess_ble.ble_client import HanchuBleStats
from custom_components.hanchu_ess_ble.const import (
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_DEVICE_NAME,
    DOMAIN,
)
from custom_components.hanchu_ess_ble.protocol import (
    HanchuDataPoint,
    HanchuProtocolError,
    HanchuReply,
)

INVERTER = "AA:BB:CC:DD:EE:01"
BATTERY = "AA:BB:CC:DD:EE:02"
DEVICE_NAME = "Hanchu ESS"

# A plausible snapshot of the registers the integration polls.
INVERTER_REGISTERS: dict[str, Any] = {
    "P000": "1",
    "P003": "HESS-HY-S-5.0K",
    "P005": 5000,
    "P500": 1,  # inverter power on
    "P511": None,  # battery recovery idle (reads null)
    "P647": "100.00",  # max charge SOC
    "P648": "6.00",  # min discharge SOC
    "P651": 1,  # work mode: Self-consumption
    "P071": "64",
    "L017": 4000,  # charge power limit
    "L018": 5000,  # discharge power limit
    "L074": 100,  # grid charge SOC limit
    "L108": 0,  # intelligent battery preheating
    "L114": 0,  # manual preheating
    **{f"L{n:03d}": 0 for n in range(5, 17)},  # charge/discharge slots
}

BATTERY_REGISTERS: dict[str, Any] = {
    "B002": "BATTERY-SERIAL",
    "B034": "64.0",
    "B035": "53.0",
    "B147": "16S184AH  ",
    "B183": 1,
}


@dataclass
class FakeHanchuClient:
    """Client-level stand-in for HanchuBleClient."""

    registers: dict[str, Any]
    stats: HanchuBleStats = field(default_factory=HanchuBleStats)
    writes: list[tuple[str, Any]] = field(default_factory=list)
    # Per-key status the device replies with; anything non-zero is a reject.
    write_status: dict[str, int] = field(default_factory=dict)
    # Raise this from every write (e.g. a dropped link).
    write_error: Exception | None = None
    # Accept writes (status 0) without the register changing, like L114
    # above 5°C.
    ignore_writes: set[str] = field(default_factory=set)
    read_error: Exception | None = None
    reads: int = 0

    def _reply(self, items: list[tuple[str, Any]]) -> HanchuReply:
        return HanchuReply(
            tid="10001",
            act="1",
            cmd="local",
            code="0",
            data=[HanchuDataPoint(key, value) for key, value in items],
        )

    async def async_read_values(self, keys, *, encrypted=True) -> HanchuReply:
        self.reads += 1
        if self.read_error is not None:
            raise self.read_error
        return self._reply([(k, self.registers[k]) for k in keys if k in self.registers])

    def _apply(self, key: str, value: Any) -> int:
        self.writes.append((key, value))
        if self.write_error is not None:
            raise self.write_error
        status = self.write_status.get(key, 0)
        if status == 0 and key not in self.ignore_writes:
            self.registers[key] = value
        return status

    async def async_write_value(self, key, value, *, encrypted=True) -> HanchuReply:
        return self._reply([(key, self._apply(key, value))])

    async def async_write_values(self, pairs, *, encrypted=True) -> list[HanchuReply]:
        replies = []
        for key, value in pairs:
            status = self._apply(key, value)
            if status != 0:
                raise HanchuProtocolError(f"Write for key={key} status={status}")
            replies.append(self._reply([(key, status)]))
        return replies


def service_info(address: str, name: str) -> SimpleNamespace:
    """A minimal BluetoothServiceInfoBleak look-alike."""
    return SimpleNamespace(
        address=address,
        name=name,
        rssi=-50,
        connectable=True,
        time=0.0,
        manufacturer_data={},
        service_data={},
        device=SimpleNamespace(name=name, address=address),
    )


@dataclass
class Harness:
    """Handles on a set-up integration, for tests to drive and inspect."""

    hass: HomeAssistant
    entry: MockConfigEntry
    inverter: FakeHanchuClient
    battery: FakeHanchuClient
    bluetooth: MagicMock

    def entity_id(self, platform: str, unique_suffix: str, address: str = INVERTER) -> str:
        entity_id = er.async_get(self.hass).async_get_entity_id(
            platform, DOMAIN, f"{address}_{unique_suffix}"
        )
        assert entity_id is not None, f"no {platform} entity {address}_{unique_suffix}"
        return entity_id

    def state(self, platform: str, unique_suffix: str, address: str = INVERTER):
        return self.hass.states.get(self.entity_id(platform, unique_suffix, address))

    @property
    def coordinator(self):
        return self.hass.data[DOMAIN][self.entry.entry_id]

    @property
    def pending_writes(self):
        return self.hass.data[DOMAIN][f"{self.entry.entry_id}_pending_writes"]

    async def poll(self) -> None:
        """Run one inverter poll now and let entities update."""
        await self.coordinator.async_refresh()
        await self.hass.async_block_till_done()


@contextmanager
def patched_bluetooth(clients: dict[str, FakeHanchuClient]):
    """Replace the coordinators' bluetooth module and BLE client class."""
    bluetooth = MagicMock()
    bluetooth.async_last_service_info.side_effect = lambda _hass, address, **_: (
        service_info(address, "HC:L110TEST" if address == INVERTER else "HC:L101TEST")
    )
    bluetooth.async_register_callback.return_value = MagicMock()
    bluetooth.async_track_unavailable.return_value = MagicMock()
    base = "custom_components.hanchu_ess_ble.coordinator"
    with (
        patch(f"{base}.bluetooth", bluetooth),
        patch(
            f"{base}.HanchuBleClient",
            side_effect=lambda _hass, address, _name: clients[address],
        ),
    ):
        yield bluetooth


async def async_setup_integration(
    hass: HomeAssistant,
    *,
    with_battery: bool = True,
    inverter_registers: dict[str, Any] | None = None,
) -> tuple[MockConfigEntry, FakeHanchuClient, FakeHanchuClient]:
    """Create a config entry and set it up. Call inside patched_bluetooth."""
    # The manifest depends on bluetooth_adapters; the fakes stand in for it.
    hass.config.components.add("bluetooth_adapters")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: DEVICE_NAME},
        options={CONF_BATTERY_ADDRESSES: [BATTERY]} if with_battery else {},
        unique_id=INVERTER,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
