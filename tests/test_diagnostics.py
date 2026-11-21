"""Diagnostics download: useful BLE health data, nothing identifying."""

from __future__ import annotations

import json
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery_flow import DiscoveryKey

from custom_components.hanchu_ess_ble.const import (
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_DEVICE_NAME,
    DOMAIN,
)
from custom_components.hanchu_ess_ble.coordinator import (
    HanchuBatteryCoordinator,
    HanchuBleCoordinator,
)
from custom_components.hanchu_ess_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hanchu_ess_ble.pending_writes import PendingWriteBuffer

from .test_coordinator import FakeReader, bluetooth_mock  # noqa: F401 — fixture

INVERTER = "AA:BB:CC:DD:EE:01"
BATTERY = "AA:BB:CC:DD:EE:02"
LOGGER_NAME = "HC:L110YM00TEST"
INVERTER_SERIAL = "INV-SERIAL-123"
BATTERY_SERIAL = "BAT-SERIAL-456"


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=LOGGER_NAME,
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: LOGGER_NAME},
        options={CONF_BATTERY_ADDRESSES: [BATTERY]},
        unique_id=INVERTER,
        discovery_keys={
            "bluetooth": (DiscoveryKey(domain="bluetooth", key=INVERTER, version=1),)
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_diagnostics_when_not_loaded(hass: HomeAssistant):
    entry = _entry(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["state"] == "not_loaded"
    assert diag["battery_count"] == 1
    assert INVERTER not in json.dumps(diag, default=repr)


async def test_diagnostics_loaded_is_redacted(hass: HomeAssistant, bluetooth_mock):  # noqa: F811
    entry = _entry(hass)
    inverter = HanchuBleCoordinator(hass, entry)
    inverter.client = FakeReader({"P071": 64})
    await inverter.async_refresh()
    # P002 isn't in the poll lists today; redaction must hold if it's added.
    inverter.data.values["P002"] = INVERTER_SERIAL

    battery = HanchuBatteryCoordinator(hass, entry, BATTERY)
    battery.client = FakeReader({"B034": 22.45, "B002": BATTERY_SERIAL})
    await battery.async_refresh()
    inverter.battery_coordinators.append(battery)

    with patch("custom_components.hanchu_ess_ble.pending_writes.async_call_later"):
        pending = PendingWriteBuffer(hass, inverter.client)
        pending.stage("P100", 1)

    hass.data[DOMAIN] = {
        entry.entry_id: inverter,
        f"{entry.entry_id}_pending_writes": pending,
    }

    diag = await async_get_config_entry_diagnostics(hass, entry)
    # Strict: everything must already be JSON-serialisable.
    text = json.dumps(diag)

    # Useful content is present...
    assert diag["inverter"]["values"]["P071"] == 64
    assert "ble_stats" in diag["inverter"]
    assert diag["inverter"]["consecutive_failures"] == 0
    assert diag["inverter"]["rssi"] == -60
    assert 0 <= diag["inverter"]["last_advertisement_seconds_ago"] < 60
    assert diag["batteries"]["battery_1"]["values"]["B034"] == 22.45
    assert diag["entry"]["discovery_keys"] == ["bluetooth"]
    assert diag["pending_writes"] == {"count": 1, "keys": ["P100"], "is_confirming": False}

    # ...and identifying details are not.
    for secret in (INVERTER, BATTERY, LOGGER_NAME, INVERTER_SERIAL, BATTERY_SERIAL):
        assert secret not in text, f"{secret} leaked into diagnostics"
    assert diag["inverter"]["values"]["P002"] == REDACTED
    assert diag["batteries"]["battery_1"]["values"]["B002"] == REDACTED

    await battery.async_shutdown()
