# Predbat bridge setup for Hanchu iESS (local BLE)

This page explains how to run [Predbat](https://github.com/springfall2008/batpred) against a Hanchu iESS inverter using this local Bluetooth integration instead of the Hanchu cloud. It has no dependency on the Hanchu cloud and keeps working if cloud connectivity is down. If you also run the cloud-based Predbat setup, you can run both side by side (for example cloud read-only for monitoring, BLE live for control) by giving each Predbat instance its own `prefix`.

> **Status:** this is a community setup, not part of the official Predbat documentation, and is not supported by Hanchu or the Predbat project. This page is the maintained reference. Test carefully and keep Predbat in read-only mode (`switch.predbat_set_read_only`) until you have verified each step.

> **TCT / TDT fields are not used here.** The cloud integration's `TCT_START_1` / `TDT_START_1` fields need raw seconds. This integration writes slot times through ordinary `time.set_value` calls, which take `HH:MM:SS` directly, so no seconds conversion is needed.

Control uses Predbat's generic Service API. BLE has no single-call equivalent to the cloud's `iotSet` / `device_control`, so Predbat's four service hooks (`charge_start`, `charge_stop`, `discharge_start`, `discharge_stop`) point at a bridge script. The script stages the charge/discharge slot 1 time entities with `time.set_value`, then calls `hanchu_ess_ble.confirm_write` to flush them to the device in a single BLE connection, with a retry and a notification on failure.

## Prerequisites

Install this integration via HACS and configure it — this requires a Bluetooth proxy within range of the inverter (see the [README](../README.md) for hardware recommendations; an ESPHome-based proxy such as the M5Stack Atom Lite has been confirmed reliable). Confirm that inverter sensors are appearing in Home Assistant, and that you can control the inverter's number/select/time entities manually via the Confirm Write button, before proceeding.

## Step 1 — Create helpers

Create the following helpers in Home Assistant (Settings → Devices & Services → Helpers):

**Toggle helpers** (toggle type):

| Entity ID | Name |
| --------- | ---- |
| `input_boolean.predbat_ble_charge_start` | Predbat BLE Charge Start |
| `input_boolean.predbat_ble_discharge_start` | Predbat BLE Discharge Start |

**Text helper** (text type):

| Entity ID | Name |
| --------- | ---- |
| `input_text.hanchu_ble_last_mode_action` | Hanchu BLE Last Mode Action |

`input_text.hanchu_ble_last_mode_action` tracks the last mode successfully applied so the bridge script can skip a redundant write when Predbat reasserts a state that is already active.

## Step 2 — Create the bridge script

All four of Predbat's service hooks call the same script, `script.hanchu_ble_set_state_queued`, passing a `mode_action` field to indicate which state to apply. The script runs with `mode: queued`, so if Predbat fires two calls close together, Home Assistant queues the second behind the first rather than letting two BLE writes race each other.

Create a new script (Settings → Automations & Scenes → Scripts → Add Script → Edit in YAML) and paste the following, replacing `YOURDEVICE` with your inverter's entity slug as it appears in your HA entity IDs (e.g. `hc_l110ym56g0915`), and replacing `notify.notify` with your own mobile notification service:

```yaml
alias: Hanchu BLE Set State Queued
mode: queued
fields:
  mode_action:
    required: true
    selector:
      select:
        options:
          - charge_start
          - charge_stop
          - discharge_start
          - discharge_stop
sequence:
  - variables:
      act: >-
        {% if mode_action is defined %}{{ mode_action }}
        {% elif data is defined and data.mode_action is defined %}{{ data.mode_action }}
        {% else %}unknown{% endif %}
  - if:
      - condition: template
        value_template: "{{ act == states('input_text.hanchu_ble_last_mode_action') }}"
    then:
      - stop: "No change — same action already applied, skipping BLE write"
  - variables:
      # Predbat's sensors already report HH:MM:SS, and time.set_value takes
      # HH:MM:SS directly — no seconds conversion needed, unlike the cloud
      # integration's TCT/TDT fields which require raw seconds.
      l005_charge_start: >-
        {{ states('sensor.predbat_ble_hc_0_charge_start_time') if act ==
        'charge_start' else '00:00:00' }}
      l006_charge_end: >-
        {{ states('sensor.predbat_ble_hc_0_charge_end_time') if act ==
        'charge_start' else '00:00:00' }}
      l011_discharge_start: >-
        {{ states('sensor.predbat_ble_hc_0_discharge_start_time') if act ==
        'discharge_start' else '00:00:00' }}
      l012_discharge_end: >-
        {{ states('sensor.predbat_ble_hc_0_discharge_end_time') if act ==
        'discharge_start' else '00:00:00' }}
  - action: time.set_value
    target:
      entity_id: time.YOURDEVICE_charge_slot_1_start
    data:
      time: "{{ l005_charge_start }}"
  - action: time.set_value
    target:
      entity_id: time.YOURDEVICE_charge_slot_1_end
    data:
      time: "{{ l006_charge_end }}"
  - action: time.set_value
    target:
      entity_id: time.YOURDEVICE_discharge_slot_1_start
    data:
      time: "{{ l011_discharge_start }}"
  - action: time.set_value
    target:
      entity_id: time.YOURDEVICE_discharge_slot_1_end
    data:
      time: "{{ l012_discharge_end }}"
  - action: hanchu_ess_ble.confirm_write
    response_variable: result
  - if:
      - condition: template
        value_template: "{{ not result.success }}"
    then:
      - delay:
          seconds: 5
      - action: hanchu_ess_ble.confirm_write
        response_variable: result2
      - if:
          - condition: template
            value_template: "{{ not result2.success }}"
        then:
          - action: notify.notify  # Replace with your own notification service
            data:
              title: "⚠️ Hanchu BLE {{ act }} FAILED"
              message: >-
                {{ act }} write failed after retry ({{ result2.message }})
                — check manually.
          - stop: "Both attempts failed — leaving last_mode_action unchanged for retry"
  - action: input_text.set_value
    target:
      entity_id: input_text.hanchu_ble_last_mode_action
    data:
      value: "{{ act }}"
  - choose:
      - conditions: "{{ act == 'charge_start' }}"
        sequence:
          - action: input_boolean.turn_on
            entity_id: input_boolean.predbat_ble_charge_start
      - conditions: "{{ act == 'charge_stop' }}"
        sequence:
          - action: input_boolean.turn_off
            entity_id: input_boolean.predbat_ble_charge_start
      - conditions: "{{ act == 'discharge_start' }}"
        sequence:
          - action: input_boolean.turn_on
            entity_id: input_boolean.predbat_ble_discharge_start
      - conditions: "{{ act == 'discharge_stop' }}"
        sequence:
          - action: input_boolean.turn_off
            entity_id: input_boolean.predbat_ble_discharge_start
```

The script always stages all four charge/discharge slot-1 time entities on every call, zeroing whichever pair is not the active mode. This keeps charge and discharge mutually exclusive on the device without relying on separate stop/start calls landing in the right order, and mirrors the logic used by the cloud integration's Predbat bridge.

**Note on register naming**: unlike the cloud integration, the BLE integration writes directly to raw Hanchu register codes (`L005`/`L006` for Charge Slot 1 Start/End, `L011`/`L012` for Discharge Slot 1 Start/End) rather than the cloud API's `TCT_START_1`/`TDT_START_1`-style field names. The variable names in the script above (`l005_charge_start` etc.) reflect this — if you're comparing against the cloud script, map by register/entity, not by field name.

## Step 3 — Create the mid-window time update automation

As with the cloud integration, Predbat may revise its planned charge or discharge end time mid-window without issuing a new `charge_start`/`discharge_start` service call. Without this automation, the inverter would continue using the original end time written at the start of the window.

Create a new automation (Settings → Automations & Scenes → Automations → Add Automation → Edit in YAML) and paste the following, replacing `YOURDEVICE` with your inverter's entity slug:

```yaml
alias: Predbat BLE - Update Hanchu Charge/Discharge Window Times
description: >
  Watches Predbat's BLE charge and discharge end time sensors and updates the
  Hanchu BLE time slots when they change mid-window during an active charge or
  discharge session.
triggers:
  - trigger: state
    entity_id: sensor.predbat_ble_hc_0_charge_end_time
    id: charge_end_changed
  - trigger: state
    entity_id: sensor.predbat_ble_hc_0_discharge_end_time
    id: discharge_end_changed
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.state not in ['unknown', 'unavailable', '00:00:00'] }}
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: charge_end_changed
          - condition: template
            value_template: "{{ is_state('input_boolean.predbat_ble_charge_start', 'on') }}"
        sequence:
          - variables:
              charge_start_time: "{{ states('sensor.predbat_ble_hc_0_charge_start_time') }}"
              charge_end_time: "{{ trigger.to_state.state }}"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_charge_slot_1_start
            data:
              time: "{{ charge_start_time }}"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_charge_slot_1_end
            data:
              time: "{{ charge_end_time }}"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_discharge_slot_1_start
            data:
              time: "00:00:00"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_discharge_slot_1_end
            data:
              time: "00:00:00"
          - action: hanchu_ess_ble.confirm_write
            response_variable: result
          - if:
              - condition: template
                value_template: "{{ not result.success }}"
            then:
              - delay:
                  seconds: 5
              - action: hanchu_ess_ble.confirm_write
                response_variable: result2
              - if:
                  - condition: template
                    value_template: "{{ not result2.success }}"
                then:
                  - action: notify.notify  # Replace with your own notification service
                    data:
                      title: "⚠️ Hanchu BLE charge end-time update FAILED"
                      message: >-
                        Write failed after retry ({{ result2.message }}) —
                        check manually.
      - conditions:
          - condition: trigger
            id: discharge_end_changed
          - condition: template
            value_template: "{{ is_state('input_boolean.predbat_ble_discharge_start', 'on') }}"
        sequence:
          - variables:
              discharge_start_time: "{{ states('sensor.predbat_ble_hc_0_discharge_start_time') }}"
              discharge_end_time: "{{ trigger.to_state.state }}"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_charge_slot_1_start
            data:
              time: "00:00:00"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_charge_slot_1_end
            data:
              time: "00:00:00"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_discharge_slot_1_start
            data:
              time: "{{ discharge_start_time }}"
          - action: time.set_value
            target:
              entity_id: time.YOURDEVICE_discharge_slot_1_end
            data:
              time: "{{ discharge_end_time }}"
          - action: hanchu_ess_ble.confirm_write
            response_variable: result
          - if:
              - condition: template
                value_template: "{{ not result.success }}"
            then:
              - delay:
                  seconds: 5
              - action: hanchu_ess_ble.confirm_write
                response_variable: result2
              - if:
                  - condition: template
                    value_template: "{{ not result2.success }}"
                then:
                  - action: notify.notify  # Replace with your own notification service
                    data:
                      title: "⚠️ Hanchu BLE discharge end-time update FAILED"
                      message: >-
                        Write failed after retry ({{ result2.message }}) —
                        check manually.
mode: queued
```

## Step 4 — Create the daily energy helpers

Unlike the cloud integration, the BLE integration does not expose daily load, PV, grid import, or grid export energy totals natively — only live instantaneous power. Predbat needs all four as daily-resetting kWh totals, so each is derived using two standard Home Assistant helpers chained together: an **Integral** (Riemann sum) sensor that accumulates a live power sensor into kWh, feeding a **Utility Meter** that resets it to zero daily.

Worked example for grid import/export (replicate the same pattern for load and PV, pointed at your load/PV power sensors instead):

1. Create two **template sensors** in `configuration.yaml` to split the bidirectional grid power sensor into import-only and export-only wattage (replace `YOURDEVICE` with your entity slug):

   ```yaml
   template:
     - sensor:
         - name: "Hanchu BLE Grid Import Power"
           unique_id: YOURDEVICE_grid_import_power
           unit_of_measurement: "W"
           state_class: measurement
           state: >
             {% set p = states('sensor.YOURDEVICE_p644') | float(0) %}
             {{ [0, p] | max }}
         - name: "Hanchu BLE Grid Export Power"
           unique_id: YOURDEVICE_grid_export_power
           unit_of_measurement: "W"
           state_class: measurement
           state: >
             {% set p = states('sensor.YOURDEVICE_p644') | float(0) %}
             {{ [0, -p] | max }}
   ```

2. Create an **Integral sensor** helper (Settings → Devices & Services → Helpers → Add Helper → Integral sensor) for each: Input sensor = the corresponding power sensor above, Integration method = Trapezoidal rule, Metric prefix = kilo, Time unit = Hours. Name them e.g. "Hanchu BLE Grid Import Total" / "Hanchu BLE Grid Export Total".

3. Create a **Utility Meter** helper for each Integral sensor: Input sensor = the Integral sensor from step 2, Meter reset cycle = Daily, with both "Periodically resetting" and "Sensor always available" enabled. Name them e.g. "Hanchu BLE Grid Import Today" / "Hanchu BLE Grid Export Today" — these are the entities referenced as `import_today`/`export_today` in `apps.yaml`.

Repeat steps 1-3 for Load Power and PV Power (referencing `sensor.YOURDEVICE_load_power` and the appropriate PV power sensor for your setup — AC-coupled systems should use `sensor.YOURDEVICE_p237`) to produce `load_today` and `pv_today`, with no need for the import/export-style sign-splitting template sensor since both are already one-directional.

## Step 5 — Configure apps.yaml

- Replace `YOURDEVICE` throughout the template with your device's entity slug as it appears in your HA entity IDs
- Start from [`hanchu_ble.yaml`](hanchu_ble.yaml) in this folder and save it as your `apps.yaml`
- Set `import_today`/`export_today`/`load_today`/`pv_today` to the Utility Meter entities created in Step 4
- Adjust `inverter_limit`, `inverter_limit_charge`, `inverter_limit_discharge`, `inverter_limit_export` and `battery_rate_max` to match your inverter and battery rated capacity in watts
- If running this as a standalone Predbat instance (e.g. in its own Docker container) rather than a Home Assistant add-on, uncomment and set `ha_url`/`ha_key` to your HA instance's address and a Long-Lived Access Token — verify the token works with a direct `curl` request to your HA instance's `/api/` endpoint before saving it into `apps.yaml`, since Predbat will crash-loop on a bad or malformed token with only a generic "HA interface not found" error to go on
- Give this instance its own unique `prefix` (e.g. `predbat_ble`) if you're running it alongside an existing cloud-based Predbat instance, so entity names for the two don't collide
- Delete the `template: True` line to allow Predbat to start
- Configure your energy rates — see [Energy Rates](https://springfall2008.github.io/batpred/energy-rates/)

## Notes

- **No soc_kw template sensor needed:** unlike the cloud integration, Predbat derives this automatically from `soc_percent` × `soc_max` — no separate template sensor is required for BLE.
- **Skipping redundant calls:** as with the cloud integration, `input_text.hanchu_ble_last_mode_action` skips the write entirely when the requested mode is already the last one successfully applied, tracked only after a confirmed successful write.
- **No charge/discharge enable toggle:** as with the cloud integration, Hanchu has no explicit enable/disable for charge or discharge — the slot zeroing mechanism (setting both start and end to `00:00:00`) is the disable method.
- **Mid-window time updates:** handled by the automation in Step 3, the same purpose as the equivalent cloud automation.
- **Not device-level atomic:** each staged register is still written as its own request within the single BLE connection `confirm_write` opens, one after another — see the [README](../README.md#known-limitations) for the full detail on this and other known limitations.
