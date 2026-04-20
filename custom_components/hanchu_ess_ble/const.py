"""Constants for the Hanchu ESS BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hanchu_ess_ble"

CONF_ADDRESS = "address"
CONF_DEVICE_NAME = "device_name"
CONF_DISCOVERED_ADDRESS = "discovered_address"

DEFAULT_NAME = "Hanchu ESS"
DEFAULT_SCAN_INTERVAL_SECONDS = 30

PLATFORMS: list[Platform] = [Platform.SENSOR]
SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

MANUFACTURER = "Hanchu"
MODEL = "ESS Inverter (BLE)"

BLE_SERVICE_UUIDS: tuple[str, ...] = (
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "0000ffff-0000-1000-8000-00805f9b34fb",
)
BLE_NOTIFY_CHARACTERISTIC_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
BLE_WRITE_CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

DEFAULT_POLL_KEYS: tuple[str, ...] = (
    "P024",
    "P025",
    "P026",
    "P027",
    "P000",
    "L023",
    "P044",
    "P045",
    "P053",
    "P055",
    "P056",
    "P057",
    "P006",
    "P007",
    "P060",
    "P061",
    "P062",
    "P067",
    "P068",
    "P069",
    "P070",
    "P071",
    "P075",
    "P076",
    "P079",
    "P080",
    "P081",
    "P082",
    "P083",
    "P084",
    "P085",
    "P139",
    "L034",
)

INVERTER_NAME_PREFIXES: tuple[str, ...] = (
    "HC:L110",
    "HC:L112",
    "HC:L113",
    "HC:L114",
    "HC:L115",
    "HC:L120",
    "HC:L122",
)
