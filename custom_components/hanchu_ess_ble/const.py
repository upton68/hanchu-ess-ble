"""Constants for the Hanchu ESS BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hanchu_ess_ble"

CONF_ADDRESS = "address"
CONF_DEVICE_NAME = "device_name"
CONF_DISCOVERED_ADDRESS = "discovered_address"

DEFAULT_NAME = "Hanchu ESS"
DEFAULT_SCAN_INTERVAL_SECONDS = 30

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

# ---------------------------------------------------------------------------
# Tiered polling keys
#
# FAST_POLL_KEYS are requested every scan cycle — these are real-time values
# that change frequently and drive automations or dashboards.
#
# SLOW_POLL_KEYS are static or rarely-changing values. Rather than requesting
# all of them every cycle, the coordinator works through this list one key per
# cycle (rotating via a counter), so each slow key is refreshed approximately
# every (SCAN_INTERVAL * len(SLOW_POLL_KEYS)) seconds.
#
# Inspired by rate-scheduled polling, as used in real-time simulation systems,
# where only values that need frequent update are polled at the fast rate.
# Thanks to community contributor [name] for suggesting this approach and the
# related improvement to sensor unavailability handling.
# ---------------------------------------------------------------------------

FAST_POLL_KEYS: tuple[str, ...] = (
    # PV input
    "P024",  # PV1 Voltage
    "P025",  # PV1 Current
    "P026",  # PV2 Voltage
    "P027",  # PV2 Current
    "P060",  # PV Total Power
    "P061",  # PV Energy Today
    "P062",  # PV Energy Total
    "P237",  # AC Coupled PV Power
    # Grid
    "P044",  # Grid Voltage L1
    "P045",  # Grid Current L1
    "P053",  # Grid Frequency
    "P055",  # Active Power
    "P056",  # Reactive Power
    "P057",  # Power Factor
    "P644",  # Grid Power
    # Battery
    "P067",  # Battery Voltage
    "P068",  # Battery Current
    "P069",  # Battery Power
    "P070",  # Battery Temperature
    "P071",  # Battery SOC
    "P075",  # Battery Charge Today
    "P076",  # Battery Discharge Today
    # System state
    "P000",  # Phase Mode
    "P651",  # Work Mode
    # Charge/discharge control — polled fast as automations depend on these
    "L017",  # Charge Power Limit
    "L018",  # Discharge Power Limit
    "P647",  # Maximum Charge SOC
    "P648",  # On-Grid Battery Discharge Minimum
    "L074",  # Max SOC Limit (grid-to-battery charge)
    # Charge slots
    "L005",  # Charge Slot 1 Start
    "L006",  # Charge Slot 1 End
    "L007",  # Charge Slot 2 Start
    "L008",  # Charge Slot 2 End
    "L009",  # Charge Slot 3 Start
    "L010",  # Charge Slot 3 End
    # Discharge slots
    "L011",  # Discharge Slot 1 Start
    "L012",  # Discharge Slot 1 End
    "L013",  # Discharge Slot 2 Start
    "L014",  # Discharge Slot 2 End
    "L015",  # Discharge Slot 3 Start
    "L016",  # Discharge Slot 3 End
    # EPS output
    "P079",  # EPS Voltage
    "P080",  # EPS Current
    "P081",  # EPS Frequency
    "P082",  # EPS Active Power
    "P083",  # EPS Reactive Power
    "P084",  # EPS Energy Today
    "P085",  # EPS Energy Total
)

SLOW_POLL_KEYS: tuple[str, ...] = (
    # Firmware versions — effectively static, only change on inverter update
    "P006",  # Main Firmware Version
    "P007",  # Safety Firmware Version
    "P139",  # ARM Firmware Version
    "L023",  # DTU Firmware Version
    # Hardware/configuration — set at install time, rarely changes
    "P005",  # Inverter Power Limit
    "P088",  # Battery Capacity (Ah)
    "L034",  # Meter Type
)

# The coordinator advances through SLOW_POLL_KEYS one entry per scan cycle,
# so each slow key is refreshed approximately every:
#   DEFAULT_SCAN_INTERVAL_SECONDS * len(SLOW_POLL_KEYS) seconds
# At 30s interval with 7 slow keys that is ~3.5 minutes per slow key.
# Adjust DEFAULT_SCAN_INTERVAL_SECONDS if a shorter or longer slow refresh
# period is needed.

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
# Human-readable register metadata
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
