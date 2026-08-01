# Changelog

All notable changes to the Hanchu ESS BLE integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [1.1.0] - 2026-08-01

### Added
Battery pack support. Individual battery packs can now be configured and polled independently of the inverter, each showing up as its own separate device in Home Assistant with its own sensors:
Battery SoC, Pack Voltage, Battery Temperature, Environmental Temperature, PCBA Temperature, Battery Current
Serial Number, Hardware Version, Model, and Firmware Version (diagnostic category)
The same BLE polling health diagnostics (Last Successful Read, Consecutive Failures, Cycle Duration) already available for the inverter
Battery packs are configured via a new Options flow (Settings → Devices & Services → the integration → Configure), where any discovered battery loggers (device names beginning HC:L101) can be selected. None are polled by default — this is fully opt-in.
Battery polling runs on its own 300-second interval, independently of the inverter's fast poll cycle, with the same consecutive-failure tolerance pattern used for the inverter — a battery BLE issue cannot affect inverter sensor availability, and issues with one battery cannot affect another.
### Changed
Added explicit display precision to the new battery voltage/current/temperature/SoC sensors, since Home Assistant's automatic precision detection can lock in at 0 decimal places if an entity's earliest readings happen to look like whole numbers.
### Notes
Battery packs must be visible to the same Bluetooth proxy already polling your inverter. As with the inverter itself, an M5Stack Atom Lite running ESPHome's Bluetooth proxy firmware is the only setup verified reliable for this integration — other Bluetooth sources may not connect to the battery packs reliably even if they can see the inverter.
Confirmed working against real hardware: two battery packs polled successfully in parallel with the inverter, with live SoC/voltage/current/temperature tracking correctly against the official app's own readings.

## [1.0.11] - 2026-07-07

### Changed

RTC Register L094 no longer uses SensorDeviceClass.TIMESTAMP. Previously this caused Home Assistant to show a relative "X ago" value on the entity row, requiring a tap into the detail view to see the actual date/time. It now displays the absolute date/time directly.
The underlying value is unchanged — still a proper, timezone-aware datetime derived from the raw epoch register — so this is a display-only change with no impact on the accuracy of the sensor.

## [1.0.10] - 2026-07-07

### Added

Firmware version sensors (Main, Safety, ARM) moved to the Diagnostics category, with clearer naming.
RTC/timezone diagnostic sensors: Timezone (L020), RTC Register L094 (Unix epoch timestamp, exposed as a proper Home Assistant timestamp entity), and RTC Daylight Saving Offset (L096) — useful for confirming the inverter's onboard clock hasn't drifted.

### Changed

Slow-poll rotation now covers 10 keys (up from 7), so each individual slow-tier sensor refreshes roughly every 5 minutes rather than 3.5. See README for details.

## [1.0.9] - 2026-07-06

### Fixed
Sensors no longer go unavailable on a single failed BLE read (#8). Previously, any read failure — even an isolated one-off — would cause the coordinator to raise UpdateFailed, which marked every sensor unavailable regardless of whether its underlying data was still valid.
The coordinator now tolerates transient failures, retaining last-known values and only escalating to UpdateFailed after MAX_CONSECUTIVE_FAILURES (default: 3) consecutive misses in a row.
Validation
Fix has been soak-tested overnight (~9 hours) in a live environment: 20 isolated single-cycle read failures occurred, none compounded past 1/3, and sensors remained available throughout the entire test window.
Technical details
Added MAX_CONSECUTIVE_FAILURES constant (const.py).
coordinator.py: _async_update_data now carries forward last-known values via the existing _build_data merge path on failures below threshold, instead of raising immediately on the first failure.
Thanks
Thanks to @PaulDGAL for the detailed original bug report that made this straightforward to diagnose and fix.

## [1.0.8] - 2026-07-03

### Fixed
- Load Power sensor now correctly nets off PV generation on non-AC-coupled
  (DC-coupled/hybrid) systems. Previously the calculation only read AC
  Coupled PV Power (P237), which stays at 0 on non-AC-coupled hardware,
  causing Load Power to be overstated by the full PV generation amount
  during daylight hours on those systems. Now sums P060 (PV Total Power)
  and P237, which are mutually exclusive by hardware type — one is always
  0 depending on coupling configuration — so the fix works correctly on
  both AC-coupled and non-AC-coupled systems without needing to know
  which type is connected.
- Corrected the battery sign convention used in the Load Power
  calculation. An interim version of this fix incorrectly applied the
  same -1.0 sign flip used by the displayed "Battery Power" sensor,
  which caused Load Power to be significantly overstated while charging
  (a 1700W charge was being added to Load instead of subtracted). The
  raw P069 register already uses the correct sign convention for this
  formula as-is; the -1.0 flip is specific to the separate, inverted
  user-facing "Battery Power" sensor and should not be reapplied here.

Thanks to a community contributor for identifying the original
non-AC-coupled gap and confirming register behaviour across both
hardware types.

## [1.0.7] - 2026-07-03

### Fixed

- Load Power sensor now correctly nets off PV generation on non-AC-coupled
  (DC-coupled/hybrid) systems. Previously the calculation only read AC
  Coupled PV Power (P237), which stays at 0 on non-AC-coupled hardware,
  causing Load Power to be overstated by the full PV generation amount
  during daylight hours on those systems. Now sums P060 (PV Total Power)
  and P237, which are mutually exclusive by hardware type — one is always
  0 depending on coupling configuration — so the fix works correctly on
  both AC-coupled and non-AC-coupled systems without needing to know
  which type is connected. Thanks to a community contributor for
  identifying the gap and confirming the register behaviour on their
  non-AC-coupled setup.

## [1.0.6] - 2026-07-02

### Fixed
- Time slot entities (`time.py`) no longer get stuck showing a requested
  value indefinitely after a failed write. Previously, if a BLE write
  timed out or the device didn't confirm success, `_pending_value` was
  never cleared, so the entity kept displaying the target time even though
  the device never actually accepted it. On failure, the entity now
  reverts to whatever the coordinator last actually read from the device.

## [1.0.5] - 2026-06-26

### Added
- Diagnostic sensors: Last BLE Read, Consecutive Failures, Cycle Duration
- Contributions from PaulDGAL

## [1.0.4] - 2026-06-26

### Changed
- Replaced flat 30-second polling with a two-tier fast/slow poll system

## [1.0.3] - 2026-06-26

### Fixed
- Load Power sign convention corrected using `abs(P237)`

## [1.0.2] - 2026-06-26

### Changed
- General reliability and polling improvements following initial release

## [1.0.1] - 2026-06-19

### Fixed
- Minor fixes following initial release

## [1.0.0] - 2026-06-19

### Added
- Initial release: local Bluetooth control for Hanchu iESS battery systems
- P/L-code protocol mapping including Grid Power, Battery Power, Battery
  Capacity, AC Coupled PV Power, and charge/discharge SOC limits
- `asyncio.Lock` to serialise BLE reads/writes
- CoordinatorEntity-based entities for charge/discharge time slots and
  sensors


[Unreleased]: https://github.com/upton68/hanchu-ess-ble/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.11...v1.1.0
[1.0.11]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.10...v1.0.11
[1.0.10]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.9...v1.0.10
[1.0.9]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.8...v1.0.9  
[1.0.8]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.7...v1.0.8
[1.0.7]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.6...v1.0.7  
[1.0.6]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/upton68/hanchu-ess-ble/compare/v1.0.0
