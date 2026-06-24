# Hanchu iESS Local BLE Protocol — Parameter Mapping Reference

Status as of June 2026. Compiled from: the `hanchu_ess_ble` fork's live BLE reads/writes (HA Green + M5Stack Atom Lite BLE proxy), the [1ulk Hanchu BLE Controller](https://github.com/1ulk/1ulk.github.io) (`hanchu-params.js` / `hanchu-controller.js`), and cross-referencing against the Hanchu cloud app and a working cloud-based HA integration.

**Scope note:** this document maps every code investigated, including many not exposed as entities by default. The integration's built entities mirror the common cloud integration entity list. Everything else here is a reference for anyone wanting to expose additional codes using the same `async_read_values()` / `async_write_value()` pattern.

---

## Protocol Summary

- **Transport:** BLE GATT. Service UUID `0000ffff-0000-1000-8000-00805f9b34fb` (fallback `0000ff00-...`). Notify characteristic `0000ff01-...`, write characteristic `0000ff02-...`.
- **Encryption:** AES-128-CFB8 (segment_size=8), no padding. Static codec basis + state vector embedded in `protocol.py`. Session key derived per-connection from a random 6-character handshake token via `derive_session_key()`.
- **Handshake:** byte `0x05` + 6-char ASCII token, sent unencrypted to the write characteristic. Device acks with a `0x05 0x00`-prefixed packet.
- **Read command:** `{"act":"1","cmd":"local","data":[{"k":key}...],"tid":"10001"}`, AES-encrypted, written to the write characteristic. Response arrives via notifications, possibly split across multiple packets, reassembled and decrypted.
- **Write command:** `{"act":"3","cmd":"local","data":[{"k":key,"v":value}],"tid":"10001"}`. Values must be plain integers, not strings. Successful write replies with `{"<key>": 0}` (0 = success acknowledgement).

---

## Important Implementation Notes

These were discovered during live testing and are critical for correct operation:

- **Integer values for writes** — values sent via `async_write_value()` must be plain Python integers, not strings. Sending a string (e.g. `"1"` instead of `1`) causes the device to respond with a plaintext error notification that the packet parser won't recognise, taking down the whole operation.
- **CoordinatorEntity for write entities** — select/number/time entities must inherit `CoordinatorEntity` so their displayed state stays live across coordinator poll cycles. Without this, values get stuck at "unknown" after an integration reload.
- **asyncio.Lock for BLE connection** — `HanchuBleClient` holds a connection lock acquired by both reads and writes. Without this, the coordinator's scheduled poll can collide with a manual write, causing 20s+ connect timeouts and failed operations.
- **Hardened packet parsing** — `parse_packet()` logs-and-skips rather than raising on unrecognised notification types, so unexpected device responses (e.g. plaintext error messages) don't crash the active operation.

---

## Confirmed Sensor / Read-Only Codes

| Code | Name | Description | Unit | Notes |
|---|---|---|---|---|
| P000 | Phase Mode | AC phase configuration — 0 = single, 3 = three | | Live value `1` on some single-phase units |
| P002 | Inverter Serial Number | | | |
| P003 | Inverter Model | e.g. "HYB-5K" | | |
| P005 | Inverter Power Limit | Rated power limit of the inverter | W | Used as dynamic max for charge/discharge power limit entities |
| P006 | Inverter Main Firmware Version | | | e.g. V610-02003-25 |
| P007 | Inverter Safety Firmware Version | | | e.g. V610-10008-11 |
| P008 | Inverter Brand | e.g. "HANCHU" | | |
| P011 | Parameter P011 | Static ~50.00 across reads | | Likely a rated/reference frequency value |
| P024 | PV String 1 Voltage | DC-coupled PV input | V | ~0 on AC-coupled systems |
| P025 | PV String 1 Current | DC-coupled PV input | A | ~0 on AC-coupled systems |
| P026 | PV String 2 Voltage | DC-coupled PV input | V | ~0 on AC-coupled systems |
| P027 | PV String 2 Current | DC-coupled PV input | A | ~0 on AC-coupled systems |
| P044 | Grid Voltage L1 | | V | |
| P045 | Grid Current L1 | | A | |
| P046 | Grid Parameter (P046) | | | Returns `None` — unsupported on tested hardware/firmware |
| P048 | Grid Parameter (P048) | | | Returns `None` — unsupported on tested hardware/firmware |
| P053 | Grid Frequency | | Hz | ~50.00 on UK grid |
| P055 | Active Power | Net active power at inverter terminals | W | Reads ~0 during AC-coupled solar export — not equivalent to total grid power |
| P056 | Grid Reactive Power | | Var | |
| P057 | Grid Power Factor | 0.00–1.00 | | Correlates strongly with grid import/export direction on tested systems |
| P060 | Total PV Power | DC-coupled combined PV output | W | ~0 on AC-coupled systems |
| P061 | PV Energy Today | DC-coupled | kWh | ~0 on AC-coupled systems |
| P062 | PV Energy Accumulated | DC-coupled | kWh | ~0 on AC-coupled systems |
| P063 | Battery Comms Status | 0 = disconnected, 1 = connected | | |
| P064 | Battery State | 0 = disconnected, 1 = normal, 2 = charging, 3 = discharging | | |
| P067 | Battery Voltage (alt) | Inverter-side reading | V | |
| P068 | Battery Current (alt) | Inverter-side reading | A | |
| P069 | Battery Power | **Positive = discharging, negative = charging** — note: the `hanchu-params.js` registry has this backwards | W | The integration applies ×-1 scale factor to match cloud convention (positive = charging) |
| P070 | Battery Temperature | | °C | |
| P071 | Battery SOC (decimal) | e.g. 0.30 = 67% | % | Integration applies ×100 scale factor to convert to percentage |
| P075 | Battery Charge Today | | kWh | |
| P076 | Battery Discharge Today | | kWh | |
| P079 | EPS Voltage | EPS output during grid outage | V | |
| P080 | EPS Current | | A | |
| P081 | EPS Frequency | | Hz | |
| P082 | EPS Active Power | | W | |
| P083 | EPS Reactive Power | | Var | |
| P084 | EPS Energy Today | | kWh | |
| P085 | EPS Energy Accumulated | | kWh | |
| P088 | Battery Capacity | Rated capacity | Ah | Convert to kWh via `Ah × 51.2 / 1000` (integration applies ×0.0512 scale factor) |
| P139 | ARM Firmware Version | | | |
| P142 | Battery Pack Count | | | |
| **P237** | **AC Coupled PV Power** | **Live AC-coupled solar power** | **W** | **Sign convention varies between hardware/firmware versions — some return positive when generating, others negative. The integration uses `abs()` in the Load Power calculation to handle both conventions correctly.** |
| **P644** | **Grid Power** | **Live grid import/export** | **W** | **Confirmed live. Positive = importing, negative = exporting** |
| L023 | DTU Firmware Version | | | e.g. 1.9.0 |
| L034 | Meter Type / CT Meter Version | 0 = no meter, 3 = specific supported meter | | |

---

## Confirmed Control / Write Codes

All writes use `act: "3"` with a plain integer value.

| Code | Name | Description | Unit |
|---|---|---|---|
| **P651** | **Work Mode** | **1=Self-Consumption, 2=Backup, 3=User-Defined, 4=Off-Grid (mirrors L019)** | |
| L017 | Charge Power Limit | Maximum battery charge power | W |
| L018 | Discharge Power Limit | Maximum battery discharge power | W |
| P647 | Maximum Charge SOC | Upper SOC limit — battery charges to this level then stops | % |
| P648 | On-Grid Battery Discharge Minimum | Hard floor — battery will not discharge below this level while on-grid | % |
| P772 | Off-Grid Battery Discharge Minimum | Hard floor for off-grid/EPS mode — distinct from P648 | % |
| L074 | Max SOC Limit | Upper SOC limit for grid-to-battery charging | % |
| L005 | Charge Period 1 Start | Seconds from midnight | s |
| L006 | Charge Period 1 End | Seconds from midnight | s |
| L007 | Charge Period 2 Start | Seconds from midnight | s |
| L008 | Charge Period 2 End | Seconds from midnight | s |
| L009 | Charge Period 3 Start | Seconds from midnight; 0 = disabled | s |
| L010 | Charge Period 3 End | Seconds from midnight; 0 = disabled | s |
| L011 | Discharge Period 1 Start | Seconds from midnight; 0 = disabled | s |
| L012 | Discharge Period 1 End | Seconds from midnight; 0 = disabled | s |
| L013 | Discharge Period 2 Start | Seconds from midnight; 0 = disabled | s |
| L014 | Discharge Period 2 End | Seconds from midnight; 0 = disabled | s |
| L015 | Discharge Period 3 Start | Seconds from midnight; 0 = disabled | s |
| L016 | Discharge Period 3 End | Seconds from midnight; 0 = disabled | s |
| P236 | Meter PV Enable | 0 = off, 1 = on | |
| P245 | Meter PV Direction | 0 = import, 1 = export | |

---

## Documented But Not Implemented

Codes identified in the 1ulk `hanchu-params.js` registry but not currently exposed as entities. Recorded here for future reference.

| Code | Name | Description | Notes |
|---|---|---|---|
| L020 | Timezone | IANA timezone code for the DTU | e.g. "Europe/London" — write only, relevant only for DTU clock sync |
| L021 | Clear WiFi Password | Write trigger to clear stored WiFi credentials | Write only — not suitable as a polled sensor |
| L094 | Unix Timestamp | Current Unix epoch timestamp set on the DTU | Could be used to verify DTU clock accuracy |
| L096 | Timezone Offset | UTC offset in hours (timeZoneOffsetActCode) | Read counterpart to L020 |

---

## Known Gaps

| Feature | Status |
|---|---|
| Daily Grid Import (Power Purchased Today) | P638 is documented in the registry but never returned in BLE replies on tested hardware — tested in isolation, batched, and across neighbouring code clusters. No alternative key found. Cloud-only or requires packet capture of official app to identify. |
| Daily Grid Export (Power Sold Today) | Same as P638 — P639 also absent from all BLE replies. |
| Daily Load Energy | No dedicated code found. Can be derived via HA's Riemann sum integral helper on the Load Power sensor. |
| AC-Coupled Daily PV Energy | P237 gives live AC-coupled PV power but no daily total equivalent was found. |
| Fast Charge / Discharge | Does not appear in the local inverter app settings — likely cloud-only via a separate endpoint. |

---

## Unmapped / Unknown Codes

Tested via live BLE reads but purpose unconfirmed:

| Code | Value(s) seen | Behaviour | Hypothesis |
|---|---|---|---|
| P011 | 50.00 | Static | Rated/reference frequency |
| P046 | None | Unsupported | Not present on tested hardware |
| P048 | None | Unsupported | Not present on tested hardware |
| P240 | ~2015.xx | Slow drift | Possibly a calibration or batch code |
| P241 | ~11.9x | Slow drift | Possibly a calibration constant |
| P498 | ~1.7–2.1 | Live, varies | Unconfirmed measurement |
| P499 | ~240–241 | Live, varies | Likely a secondary voltage reading |
| P640 | ~6000+ | Cumulative | Energy counter — increments daily |
| P641 | ~2700+ | Cumulative | Energy counter — increments daily |
| P642 | ~5900+ | Cumulative | Energy counter — increments daily |
| P643 | ~2700+ | Cumulative | Energy counter — increments daily |
| P644 | Varies, sign-changing | Live | **Confirmed Grid Power** |
| P685–P693 | No data | Absent | Unsupported/reserved on tested hardware |

---

## Derived Sensors

| Sensor | Formula | Notes |
|---|---|---|
| Load Power | `P644 + abs(P237) + P069` (raw values) | `abs()` applied to P237 to handle sign convention variation between hardware/firmware versions |
| Battery Capacity (kWh) | `P088 × 0.0512` | P088 in Ah × 51.2V nominal / 1000 |
| Battery SOC (%) | `P071 × 100` | P071 is a decimal fraction e.g. 0.67 = 67% |
| Battery Power (cloud convention) | `P069 × -1` | Inverts to positive=charging to match cloud convention |

---

## Credits

- **[Blustery7752](https://github.com/Blustery7752/hanchu-ess-ble)** — original `hanchu-ess-ble` integration providing the BLE connection, encryption, and read foundation
- **[1ulk](https://github.com/1ulk/1ulk.github.io)** — Hanchu BLE Controller web app whose `hanchu-params.js` parameter registry and `hanchu-controller.js` write implementation were essential references
- **PaulDGAL** — real-world testing identifying BLE load and sign convention issues, and proposing the tiered polling approach
- 
