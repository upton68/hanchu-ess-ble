# Hanchu ESS BLE

Home Assistant custom integration for Hanchu ESS systems using local Bluetooth Low Energy (BLE).

This integration connects directly to supported Hanchu inverters over BLE and exposes inverter data as Home Assistant sensors.

## Current Status

This project is working, but still early.

What it can do today:
- Discover supported Hanchu inverters over Bluetooth
- Connect locally over BLE
- Poll a set of inverter registers
- Expose core battery, PV, grid, EPS, and diagnostic sensors

What is not implemented yet:
- Battery device support as separate BLE devices
- Full register coverage
- Full scaling/units verification for every sensor
- Write/configuration commands

## Supported Device Discovery

The integration currently targets inverter advertisements with names starting with:

- `HC:L110`
- `HC:L112`
- `HC:L113`
- `HC:L114`
- `HC:L115`
- `HC:L120`
- `HC:L122`

Battery/BMS devices are intentionally not included yet.

## Installation

### HACS

1. Open HACS in Home Assistant
2. Go to `Integrations`
3. Open the menu in the top right
4. Choose `Custom repositories`
5. Add this repository URL
6. Select category `Integration`
7. Install `Hanchu ESS BLE`
8. Restart Home Assistant

### Manual

1. Copy `custom_components/hanchu_ess_ble` into your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Make sure Bluetooth is working on the Home Assistant host
2. Bring the inverter into BLE range
3. In Home Assistant, go to `Settings -> Devices & Services`
4. Add `Hanchu ESS BLE`, or wait for Bluetooth discovery
5. Select the discovered inverter
6. Finish setup

## Sensors

The integration currently includes a mix of enabled-by-default and disabled-by-default sensors.

Examples of currently exposed sensors:
- Battery SoC
- Battery Temperature
- Battery Power
- PV1 Voltage / Current
- PV2 Voltage / Current
- PV Energy Today / Total
- Grid Frequency
- EPS Voltage / Current / Frequency
- EPS Active Power
- RSSI

Some sensors are disabled by default.

## Notes

- The integration uses local BLE only
- No cloud account is required
- Sensor scaling is still being refined as more real-world data is collected

## Debug Logging

If you need to troubleshoot BLE communication, add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.hanchu_ess_ble: debug
```

This enables detailed logs for:
- BLE discovery
- connection and disconnection
- encrypted handshake
- request writes
- notifications
- packet reassembly
- parsed inverter replies

## Known Caveats

- Not all values have been added yet
- Some values may need additional scaling adjustments depending on the device family

## Contributing

Useful contributions include:
- additional inverter model testing
- confirmed register meanings
- confirmed unit/scaling corrections
- packet captures from supported devices
- battery/BMS BLE support

## Disclaimer

This is an unofficial community integration and is not affiliated with or endorsed by Hanchu.

