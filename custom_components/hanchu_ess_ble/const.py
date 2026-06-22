"""Constants for the Hanchu ESS BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hanchu_ess_ble"

CONF_ADDRESS = "address"
CONF_DEVICE_NAME = "device_name"
CONF_DISCOVERED_ADDRESS = "discovered_address"

DEFAULT_NAME = "Hanchu ESS"
DEFAULT_SCAN_INTERVAL_SECONDS = 75

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.TIME]
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
    # Existing baseline sensors (from the original fork)
    "P024", # PV String 1 Voltage
    "P025", "P026", "P027", "P000", "L023",
    "P044", "P045", "P053", "P055", "P056", "P057",
    "P006", "P007", "P060", "P061", "P062",
    "P067", "P068", "P069", "P070", "P071",
    "P075", "P076",
    "P079", "P080", "P081", "P082", "P083", "P084", "P085",
    "P139", "L034",
    # Confirmed additions — cloud parity build (June 2026)
    "P005",  # Inverter Power Limit (W) — used as dynamic max for charge/discharge power limits
    "P088",  # Battery Capacity (Ah)
    "P237",  # AC Coupled PV Power
    "P644",  # Grid Power
    "P651",  # Work Mode
    "L017",  # Charge Power Limit
    "L018",  # Discharge Power Limit
    "P647",  # Maximum Charge SOC
    "P648",  # On-Grid Battery Discharge Minimum
    "L074",  # Max SOC Limit (grid-to-battery charge)
    "L005", "L006", "L007", "L008", "L009", "L010",  # Charge slots 1-3
    "L011", "L012", "L013", "L014", "L015", "L016",  # Discharge slots 1-3
)

# Interlaced register groups.
# Each tuple is a "slot". Only one slot is polled per cycle.
REGISTER_INTERLACE_SLOTS: list[list[str]] = [
    # Slot 0
    ["P088", "P044"],
    # Slot 1
    ["P006", "P053"],
    # Slot 2
    ["P007", "P056"],
    # Slot 3
    ["L023"],
    # Slot 4
    ["P079"],
    # Slot 5
    ["P080"],
    # Slot 6
    ["P081"],
    # Slot 7
    ["P082"],
    # Slot 8
    ["P083"],
    # Slot 9
    ["P084"],
    # Slot 10
    ["P085"],
    # Slot 11
    ["L034"],
    # Slot 12
    ["P057"],
]

INVERTER_NAME_PREFIXES: tuple[str, ...] = (
    "HC:L110",
    "HC:L112",
    "HC:L113",
    "HC:L114",
    "HC:L115",
    "HC:L120",
    "HC:L122",
)

# ---------------------------------------------------------------------------
# NEW: Human-readable register metadata
# ---------------------------------------------------------------------------

REGISTER_INFO: dict[str, str] = {
    "P000": "Phase Mode",
    "P002": "Inverter Serial Number",
    "P003": "Inverter Model",
    "P005": "Inverter Power Limit",
    "P006": "Main Firmware Version",
    "P007": "Safety Firmware Version",
    "P008": "Inverter Brand",
    "P011": "Reference Frequency",
    "P024": "PV1 Voltage",
    "P025": "PV1 Current",
    "P026": "PV2 Voltage",
    "P027": "PV2 Current",
    "P044": "Grid Voltage L1",
    "P045": "Grid Current L1",
    "P053": "Grid Frequency",
    "P055": "Active Power",
    "P056": "Reactive Power",
    "P057": "Power Factor",
    "P060": "PV Total Power",
    "P061": "PV Energy Today",
    "P062": "PV Energy Total",
    "P063": "Battery Comms Status",
    "P064": "Battery State",
    "P067": "Battery Voltage",
    "P068": "Battery Current",
    "P069": "Battery Power",
    "P070": "Battery Temperature",
    "P071": "Battery SOC",
    "P075": "Battery Charge Today",
    "P076": "Battery Discharge Today",
    "P079": "EPS Voltage",
    "P080": "EPS Current",
    "P081": "EPS Frequency",
    "P082": "EPS Active Power",
    "P083": "EPS Reactive Power",
    "P084": "EPS Energy Today",
    "P085": "EPS Energy Total",
    "P088": "Battery Capacity",
    "P139": "ARM Firmware Version",
    "P237": "AC Coupled PV Power",
    "P240": "Calibration Value 1",
    "P241": "Calibration Value 2",
    "P498": "Measurement (Unmapped)",
    "P499": "Voltage (Unmapped)",
    "P640": "Energy Counter 1",
    "P641": "Energy Counter 2",
    "P642": "Energy Counter 3",
    "P643": "Energy Counter 4",
    "P644": "Grid Power",
    "P647": "Maximum Charge SOC",
    "P648": "On-Grid Battery Discharge Minimum",
    "P651": "Work Mode",

    # Charge slots
    "L005": "Charge Slot 1 Start",
    "L006": "Charge Slot 1 End",
    "L007": "Charge Slot 2 Start",
    "L008": "Charge Slot 2 End",
    "L009": "Charge Slot 3 Start",
    "L010": "Charge Slot 3 End",

    # Discharge slots
    "L011": "Discharge Slot 1 Start",
    "L012": "Discharge Slot 1 End",
    "L013": "Discharge Slot 2 Start",
    "L014": "Discharge Slot 2 End",
    "L015": "Discharge Slot 3 Start",
    "L016": "Discharge Slot 3 End",

    "L017": "Charge Power Limit",
    "L018": "Discharge Power Limit",
    "L023": "DTU Firmware Version",
    "L034": "Meter Type",
    "L074": "Max SOC Limit (Grid Charge)",
}

