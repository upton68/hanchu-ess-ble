"""Constants for the Hanchu ESS BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hanchu_ess_ble"

CONF_ADDRESS = "address"
CONF_DEVICE_NAME = "device_name"

DEFAULT_NAME = "Hanchu ESS"
DEFAULT_SCAN_INTERVAL_SECONDS = 30

PLATFORMS: list[Platform] = [Platform.SENSOR]
SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

MANUFACTURER = "Hanchu"
MODEL = "ESS Inverter (BLE)"
