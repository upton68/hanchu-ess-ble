"""Config flow for the Hanchu ESS BLE integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_DISCOVERED_ADDRESS,
    DEFAULT_NAME,
    DOMAIN,
    INVERTER_NAME_PREFIXES,
)


def _is_inverter_name(name: str | None) -> bool:
    """Return True when the name matches a supported inverter prefix."""

    if not name:
        return False
    return any(name.startswith(prefix) for prefix in INVERTER_NAME_PREFIXES)


def _normalise_address(address: str) -> str:
    """Normalise common MAC address input formats."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", address).upper()
    if len(cleaned) != 12:
        raise ValueError("invalid_mac")
    return ":".join(cleaned[index : index + 2] for index in range(0, 12, 2))


class HanchuEssBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hanchu ESS BLE."""

    VERSION = 1
    MINOR_VERSION = 1

    _discovery_info: BluetoothServiceInfoBleak | None = None

    def _discovered_inverters(self) -> dict[str, str]:
        """Return the currently discovered inverter devices."""

        devices: dict[str, str] = {}
        for service_info in bluetooth.async_discovered_service_info(
            self.hass,
            connectable=False,
        ):
            name = service_info.name or service_info.device.name
            if not _is_inverter_name(name):
                continue

            label = f"{name} ({service_info.address})" if name else service_info.address
            devices[service_info.address] = label

        return dict(sorted(devices.items(), key=lambda item: item[1].casefold()))

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle setup from discovered inverters."""
        errors: dict[str, str] = {}
        discovered = self._discovered_inverters()

        if user_input is not None:
            address_input = user_input.get(CONF_DISCOVERED_ADDRESS)
            if not address_input:
                errors["base"] = "invalid_address"
            else:
                address = _normalise_address(address_input)
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input[CONF_DEVICE_NAME],
                    data={
                        CONF_ADDRESS: address,
                        CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_DISCOVERED_ADDRESS): vol.In(discovered),
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery."""
        discovered_name = discovery_info.name or discovery_info.device.name
        if not _is_inverter_name(discovered_name):
            return self.async_abort(reason="not_supported")

        address = discovery_info.address
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm Bluetooth discovery."""
        assert self._discovery_info is not None
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_DEVICE_NAME],
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                },
            )

        discovered_name = (
            self._discovery_info.name
            or self._discovery_info.device.name
            or DEFAULT_NAME
        )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": discovered_name,
                "address": self._discovery_info.address,
            },
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_NAME, default=discovered_name): str}
            ),
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow the user to update the configured BLE address or name."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                address = _normalise_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors["base"] = "invalid_address"
            else:
                if address != entry.unique_id:
                    errors["base"] = "address_change_not_supported"
                else:
                    await self.async_set_unique_id(address)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_ADDRESS: address,
                            CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                        },
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_DEVICE_NAME): str,
                        vol.Required(CONF_ADDRESS): str,
                    }
                ),
                {
                    CONF_DEVICE_NAME: entry.data.get(CONF_DEVICE_NAME, DEFAULT_NAME),
                    CONF_ADDRESS: entry.data[CONF_ADDRESS],
                },
            ),
            errors=errors,
        )
