"""Shared fixtures for the Hanchu ESS BLE tests."""

from __future__ import annotations

import os
import sys

# Allow `import custom_components.hanchu_ess_ble...` from the repo root
# without an install step.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from unittest.mock import PropertyMock, patch  # noqa: E402

import pytest  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402

from .integration_setup import (  # noqa: E402
    BATTERY,
    BATTERY_REGISTERS,
    INVERTER,
    INVERTER_REGISTERS,
    FakeHanchuClient,
    Harness,
    async_setup_integration,
    patched_bluetooth,
)


@pytest.fixture
def entity_registry_enabled_by_default():
    """Create every entity enabled, including those disabled by default."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
async def harness(hass, enable_custom_integrations, entity_registry_enabled_by_default):
    """The integration set up with an inverter and one battery, all entities enabled.

    Several controls (preheating switches, recovery and clock buttons) are
    disabled by default, so the registry fixture enables everything.
    """
    inverter = FakeHanchuClient(dict(INVERTER_REGISTERS))
    battery = FakeHanchuClient(dict(BATTERY_REGISTERS))
    with patched_bluetooth({INVERTER: inverter, BATTERY: battery}) as bluetooth:
        entry = await async_setup_integration(hass)
        yield Harness(hass, entry, inverter, battery, bluetooth)
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
