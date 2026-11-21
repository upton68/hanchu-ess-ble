"""Config flow (user, Bluetooth discovery, reconfigure) and options flow (batteries)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hanchu_ess_ble.config_flow import _normalise_address
from custom_components.hanchu_ess_ble.const import (
    CONF_ADDRESS,
    CONF_BATTERY_ADDRESSES,
    CONF_DEVICE_NAME,
    CONF_DISCOVERED_ADDRESS,
    DOMAIN,
)

from .integration_setup import BATTERY, INVERTER, service_info

OTHER_BATTERY = "AA:BB:CC:DD:EE:03"

ADVERTISING = [
    service_info(INVERTER, "HC:L110TEST"),
    service_info(BATTERY, "HC:L101BAT1"),
    service_info("AA:BB:CC:DD:EE:99", "Some Other Device"),
]


@pytest.fixture(autouse=True)
def _flow_environment(hass, enable_custom_integrations):
    """Don't actually set the integration up; fake what's advertising."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("bluetooth")
    with (
        patch("custom_components.hanchu_ess_ble.async_setup_entry", return_value=True),
        patch("custom_components.hanchu_ess_ble.async_unload_entry", return_value=True),
        patch(
            "custom_components.hanchu_ess_ble.config_flow.bluetooth.async_discovered_service_info",
            side_effect=lambda *_args, **_kwargs: list(ADVERTISING),
        ),
    ):
        yield


def _entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hanchu ESS",
        data={CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Hanchu ESS"},
        options=options or {},
        unique_id=INVERTER,
    )
    entry.add_to_hass(hass)
    return entry


# --- Address helper -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["aa:bb:cc:dd:ee:01", "AA-BB-CC-DD-EE-01", "aabbccddee01", " AA BB CC DD EE 01 "],
)
def test_normalise_address_accepts_common_formats(raw):
    assert _normalise_address(raw) == INVERTER


@pytest.mark.parametrize("raw", ["", "AA:BB:CC", "AA:BB:CC:DD:EE:01:02", "not a mac"])
def test_normalise_address_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        _normalise_address(raw)


# --- User flow -----------------------------------------------------------------------


async def test_user_flow_lists_only_inverters(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    choices = result["data_schema"].schema[CONF_DISCOVERED_ADDRESS].container
    assert list(choices) == [INVERTER]
    assert "HC:L110TEST" in choices[INVERTER]


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_NAME: "Garage Inverter", CONF_DISCOVERED_ADDRESS: INVERTER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Inverter"
    assert result["data"] == {CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Garage Inverter"}
    assert result["result"].unique_id == INVERTER


async def test_user_flow_aborts_if_already_configured(hass):
    _entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_NAME: "Again", CONF_DISCOVERED_ADDRESS: INVERTER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- Bluetooth discovery -------------------------------------------------------------


async def test_bluetooth_discovery_confirms_and_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(INVERTER, "HC:L110TEST")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "HC:L110TEST", "address": INVERTER}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_NAME: "Hanchu ESS"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Hanchu ESS"}


@pytest.mark.parametrize("name", ["HC:L101BAT1", "Some Other Device", None])
async def test_bluetooth_discovery_ignores_non_inverters(hass, name):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(BATTERY, name)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_of_configured_inverter_aborts(hass):
    _entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(INVERTER, "HC:L110TEST")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- Reconfigure ---------------------------------------------------------------------


async def _reconfigure(hass, entry, user_input):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_reconfigure_renames(hass):
    entry = _entry(hass)
    result = await _reconfigure(
        hass, entry, {CONF_DEVICE_NAME: "Renamed", CONF_ADDRESS: "aa-bb-cc-dd-ee-01"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_ADDRESS: INVERTER, CONF_DEVICE_NAME: "Renamed"}


async def test_reconfigure_rejects_bad_address(hass):
    entry = _entry(hass)
    result = await _reconfigure(hass, entry, {CONF_DEVICE_NAME: "X", CONF_ADDRESS: "nonsense"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_address"}
    assert entry.data[CONF_DEVICE_NAME] == "Hanchu ESS"


async def test_reconfigure_refuses_a_different_address(hass):
    entry = _entry(hass)
    result = await _reconfigure(
        hass, entry, {CONF_DEVICE_NAME: "X", CONF_ADDRESS: "11:22:33:44:55:66"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "address_change_not_supported"}
    assert entry.data[CONF_ADDRESS] == INVERTER


# --- Options flow: battery selection -------------------------------------------------


async def test_options_lists_advertising_batteries(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    choices = result["data_schema"].schema[CONF_BATTERY_ADDRESSES].options
    assert list(choices) == [BATTERY]


async def test_options_keeps_configured_battery_that_is_not_advertising(hass):
    # A configured pack out of range during the scan must still be listed,
    # so it's only removed if the user unticks it.
    entry = _entry(hass, options={CONF_BATTERY_ADDRESSES: [OTHER_BATTERY]})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    choices = result["data_schema"].schema[CONF_BATTERY_ADDRESSES].options
    assert set(choices) == {BATTERY, OTHER_BATTERY}


async def test_options_saves_selection(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BATTERY_ADDRESSES: [BATTERY]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_BATTERY_ADDRESSES: [BATTERY]}


async def test_options_can_remove_all_batteries(hass):
    entry = _entry(hass, options={CONF_BATTERY_ADDRESSES: [BATTERY]})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BATTERY_ADDRESSES: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_BATTERY_ADDRESSES: []}
