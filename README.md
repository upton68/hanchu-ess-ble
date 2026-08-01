# Hanchu ESS BLE — Home Assistant Integration

A local Bluetooth (BLE) integration for Hanchu iESS battery storage systems, providing real-time sensor data and full write control of work mode, charge/discharge limits, SOC targets, and time slots — entirely over BLE, with no dependency on the Hanchu cloud.

> ⚠️ **This integration is not supported by Hanchu.** Use at your own risk. Incorrect settings may affect your inverter's operation. Always verify critical changes via the official Hanchu app.

---

## Background

This integration is a fork of [Blustery7752's hanchu-ess-ble](https://github.com/Blustery7752/hanchu-ess-ble), which provided the original BLE read-only foundation. Write control was developed by reverse-engineering the local BLE protocol with significant reference to [1ulk's Hanchu BLE Controller](https://1ulk.github.io/hanchu-) — a browser-based Web Bluetooth control panel whose `hanchu-params.js` parameter registry and `hanchu-controller.js` write implementation were invaluable in confirming the protocol structure and P/L-code mappings.

---

## Features

- **Real-time sensors**: Battery SOC, Battery Power, Battery Temperature, Battery Capacity, Grid Power, AC Coupled PV Power, Load Power (derived), Grid Frequency, L1 Voltage/Current, EPS sensors, and more
- **Individual battery pack monitoring**: optionally poll each battery pack's own BLE logger directly for per-pack SoC, voltage, current, and temperature — each pack shown as its own device, independent of the inverter's aggregate battery readings
- **Work Mode control**: switch between Self-consumption, Backup, User-defined, and Off-grid modes
- **Charge/Discharge Power Limits**: set maximum charge and discharge power in Watts
- **SOC controls**: Maximum Charge SOC, Minimum Discharge SOC (on-grid), Grid to Battery Charge Maximum
- **Time slot scheduling**: full control of all six charge and discharge time periods (User-defined mode)
- **Tiered polling**: real-time sensors update every 30 seconds; static values (firmware versions, hardware config) rotate through on a slower schedule, reducing BLE load
- **Fully local**: no cloud dependency — works even if the Hanchu cloud is unavailable

---

## Requirements

- A Hanchu iESS battery storage system with a compatible inverter
- Home Assistant with Bluetooth support, or a Bluetooth proxy
- A Bluetooth proxy is **strongly recommended** for reliable connectivity — onboard Bluetooth on HA hardware has been reported as unreliable with this integration across multiple setups. Tested with the **M5Stack Atom Lite** running ESPHome's Bluetooth proxy firmware, placed within range of the inverter (available from The Pi Hut for around £10). If you plan to use individual battery pack monitoring, the same proxy needs to be within range of the battery packs too — other Bluetooth sources have not been verified reliable for this integration, including for the inverter itself.

---

## Installation

### Via HACS (recommended)

1. In HACS, go to **Integrations → Custom repositories**
2. Add `https://github.com/upton68/hanchu-ess-ble` as an **Integration**
3. Search for "Hanchu ESS BLE" and install
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/hanchu_ess_ble` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Hanchu ESS BLE**
3. Select your inverter from the discovered Bluetooth devices (device name begins with `HC:`)
4. The integration will connect and populate all entities automatically

### Adding individual battery packs (optional)

Battery pack monitoring is off by default — the inverter's own aggregate battery sensors (Battery SoC, Battery Power, Battery Temperature) work without any extra setup.

To monitor individual battery packs directly:

1. Go to **Settings → Devices & Services**, find the Hanchu ESS BLE integration
2. Click **Configure**
3. Select any discovered battery packs from the list (device names begin `HC:L101`)
4. Save — each selected battery will appear as its own device with its own sensors

Battery packs poll on a 300-second interval, independently of the inverter's faster poll cycle. To add or remove batteries later, revisit **Configure** at any time.

---

## Entities

### Controls

| Entity | Description |
|---|---|
| Work Mode | Select: Self-consumption, Backup, User-defined, Off-grid |
| Charge Power Limit | Maximum battery charge power (W) |
| Discharge Power Limit | Maximum battery discharge power (W) |
| Maximum Charge SOC | Upper SOC limit — battery charges to this level then stops (%) |
| Minimum Discharge SOC | On-grid hard floor — battery will not discharge below this level (%) |
| Grid to Battery Charge Maximum | Upper SOC limit for grid-to-battery charging (%) |
| Charge Slot 1–3 Start/End | Charge time periods for User-defined mode |
| Discharge Slot 1–3 Start/End | Discharge time periods for User-defined mode |

### Sensors (enabled by default)

| Entity | Description |
|---|---|
| Battery SoC | State of charge (%) |
| Battery Power | Charge/discharge power — positive = charging, negative = discharging (W) |
| Battery Temperature | Battery temperature (°C) |
| Battery Capacity | Rated battery capacity (kWh) |
| Grid Power | Grid import/export — positive = importing, negative = exporting (W) |
| AC Coupled PV Power | AC-coupled solar generation (W) |
| Load Power | Derived house load — Grid + AC PV + Battery (W) |
| Grid Frequency | AC grid frequency (Hz) |
| L1 Voltage | Grid line voltage (V) |
| L1 Current | Grid line current (A) |
| EPS Voltage/Current/Frequency/Power | Emergency power supply readings |

### Sensors (disabled by default)

Battery Voltage, Battery Current, Active Power, Reactive Power, Power Factor, Battery Charge Today, Battery Discharge Today, PV Energy Today/Total, EPS Energy Today/Total.

### Battery pack sensors (per pack, opt-in via Configure)

Each configured battery pack appears as its own device with the following sensors:

| Entity | Description |
|---|---|
| Battery SoC | State of charge, as reported directly by that pack (%) |
| Pack Voltage | Total voltage of that pack (V) |
| Battery Temperature | Cell temperature (°C) |
| Environmental Temperature | Ambient temperature at the pack (°C) |
| PCBA Temperature | Control board temperature (°C) |
| Battery Current | Charge/discharge current for that pack (A) |
| Serial Number, Hardware Version, Model, Firmware Version | Diagnostic identification fields |

These are independent per-pack readings, distinct from the inverter's aggregate Battery SoC/Power/Temperature sensors — useful for spotting imbalance between packs on multi-battery systems.

### New in v1.0.10: Firmware & RTC Diagnostics

Firmware version sensors (Main, Safety, ARM) are now categorised under
Diagnostics. Three new diagnostic sensors have also been added to help
verify the inverter's onboard clock:

- **Timezone** (`L020`) — IANA timezone string reported by the DTU (e.g. `Europe/London`)
- **RTC Register L094** — the DTU's current Unix epoch time, exposed as a proper Home Assistant timestamp
- **RTC Daylight Saving Offset** (`L096`) — DST offset in minutes (e.g. `60` during BST, `0` during GMT)

These are most useful for troubleshooting: if `RTC Register L094` reads
noticeably different from actual current time, it indicates the inverter's
onboard clock has drifted, which could affect any scheduling that relies on
the inverter's own internal clock rather than commands sent live from Home
Assistant.

---

## Polling

Registers are split into two tiers to reduce BLE load on the inverter:

**Fast poll (every 30 seconds)** — real-time values: power flows, battery SOC, voltage, current, temperature, grid readings, work mode, charge/discharge slots and limits.

### Slow-Poll Rotation

Static or rarely-changing values (firmware versions, hardware configuration,
RTC/timezone diagnostics) are polled one key per cycle on a rotating basis,
rather than every cycle. With the default 30-second scan interval and 10
slow-poll keys, each individual slow-tier sensor refreshes approximately
every 5 minutes.
Slow poll values persist their last known reading between updates — sensors will not show as unavailable simply because a slow register was not included in the current cycle.

**Startup behaviour** — after a restart, slow-tier sensors will show as unavailable for the first few minutes while the rotation works through all seven slow keys for the first time (~3.5 minutes at the default interval). This is expected and not a sign of a problem.

**Cycle duration variation** — BLE read times can vary between cycles (typically 6–17 seconds has been observed). This is believed to be partly caused by the inverter handling cloud polling requests simultaneously, which occasionally delays its BLE response. Value persistence means this variation does not cause sensors to go unavailable.

### Battery Pack Polling

Individual battery packs (if configured) poll on a separate, much slower 300-second interval, since SoC/voltage/temperature change gradually and don't need the inverter's faster cadence. Each battery reads all of its sensor values in a single BLE connection per cycle, rather than the tiered fast/slow approach used for the inverter. Battery polling failures follow the same consecutive-failure tolerance as the inverter, and are tracked independently per pack — one battery's BLE issues cannot affect another battery or the inverter.

---

## Known Limitations

- **Daily grid import/export energy** — not available over the local BLE protocol on tested hardware/firmware. The official app shows these figures, but the corresponding BLE keys were not found to return data despite exhaustive testing.
- **AC Coupled PV Power** — the sign convention of P237 varies between hardware and firmware versions. Some systems return a positive value when generating, others return negative. The raw sensor reflects whatever the inverter reports. The derived Load Power sensor uses the absolute value of P237 so it calculates correctly regardless of which convention your system uses.
- **Fast charge/discharge** — not available locally. This feature appears to be cloud-only on tested hardware.
- **DC-coupled PV sensors** (PV1/PV2 Voltage/Current, PV Total Power) — will read zero on AC-coupled systems where solar is connected via a separate inverter rather than directly into the Hanchu's DC inputs.
- **This integration is not supported by Hanchu.** It was developed independently by reverse-engineering the local BLE protocol.
- **Battery pack visibility** — battery packs must be within range of, and previously discovered by, your Bluetooth proxy before they'll appear as selectable options in Configure. If a battery isn't listed, check it's showing as a discovered device under Settings → Devices & Services → Bluetooth before trying again.

---

## Protocol Notes

The Hanchu iESS communicates over BLE using AES-128-CFB8 encryption with a per-session key derived from a handshake token. Reads use `act: "1"` and writes use `act: "3"` in a JSON payload sent to the write characteristic (`0000ff02-...`), with responses arriving via notifications on the read characteristic (`0000ff01-...`).

For a comprehensive mapping of all known P/L-codes, see the [protocol mapping reference](docs/hanchu-ble-local-protocol-mapping.md) included in this repository.

---

## Credits

- **[Blustery7752](https://github.com/Blustery7752/hanchu-ess-ble)** — original `hanchu-ess-ble` Home Assistant integration, which provided the BLE connection, encryption, and read foundation this fork builds on
- **[1ulk](https://github.com/1ulk/1ulk.github.io)** — browser-based Hanchu BLE Controller, whose `hanchu-params.js` parameter registry and `hanchu-controller.js` write implementation were essential references for the write protocol and P/L-code mappings
- **PaulDGAL** — real-world testing and community feedback that identified BLE load as the root cause of sensor unavailability issues, and proposed the tiered polling approach and the sensor value persistence improvement that became v1.0.2

---

## Disclaimer

This is an independent community project with no affiliation with or support from Hanchu. Use at your own risk. The authors accept no liability for any damage to equipment or loss of functionality resulting from use of this integration.
