# Contributing

Thanks for your interest in improving the Hanchu ESS BLE integration! This
document covers how to report problems, set up a development environment,
run the tests, and get a change merged.

This is an independent, unofficial integration. Problems with the Hanchu app,
cloud, or inverter firmware itself should go to Hanchu, not here.

## Reporting issues

Please use the GitHub issue templates (**Bug report** / **Feature request**).
Questions and general chat are welcome in
[Discussions](https://github.com/upton68/hanchu-ess-ble/discussions);
bugs and enhancements belong in Issues so they can be tracked.

Most BLE problems depend on the Bluetooth setup, so for bugs please include:

- Home Assistant and integration versions
- inverter model and the first part of the logger name (e.g. `HC:L110`)
- Bluetooth source: ESPHome proxy (hardware + ESPHome version) or local adapter
- RSSI to the inverter, if you know it
- a **Download diagnostics** export (Settings → Devices & services →
  Hanchu ESS BLE → ⋮ → Download diagnostics). It includes BLE health
  (failure count, cycle time, last good read) and the latest register
  values, with Bluetooth addresses, logger names and serial numbers removed.

For more detail, enable debug logging:

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.hanchu_ess_ble: debug
```

Debug logs include Bluetooth addresses; remove them before posting if you
prefer.

## Development setup

Home Assistant core only runs on Unix-like systems, so develop on **macOS or
Linux** (on Windows, use WSL). Python 3.13 matches CI.

```bash
git clone https://github.com/upton68/hanchu-ess-ble.git
cd hanchu-ess-ble
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest
```

`requirements-test.txt` installs
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
which brings in a matching Home Assistant core and test harness, plus the
requirements of HA's `bluetooth` and `usb` components that the integration
imports.

## Running the tests

No Bluetooth hardware is needed. The tests replace Bleak with a fake logger
(`tests/fake_inverter.py`) that speaks the real wire protocol: handshake,
session key, AES-CFB8 encryption, and multi-packet framed replies. Everything
above the radio runs the integration's real code.

```bash
pytest -q                                   # whole suite, summary only
pytest tests/test_pending_writes.py         # one file
pytest -k "confirm"                         # tests whose name matches
pytest -x --lf                              # stop at first failure / re-run last failures
```

| File | Covers |
|---|---|
| `test_protocol.py` | session key derivation, handshake, encryption, request building, reply parsing, packet framing and reassembly |
| `test_ble_client.py` | read/write round trips, request batching, write status checks, hang → timeout, connection lock |
| `test_pending_writes.py` | staging, Confirm Write, Discard, auto-discard timeout, and the concurrent-confirm / staged-during-flush races |
| `test_clock_sync.py` | DST offset, what gets written and when, refusals (zone mismatch, Confirm in progress), read-back verification |
| `test_coordinator.py` | carry-forward on failed reads, failure threshold, value merging, slow-key rotation, model read, battery polling schedule |
| `test_diagnostics.py` | diagnostics content and redaction |

### Where to add tests

- Protocol/wire changes → `test_protocol.py`, plus a round trip in `test_ble_client.py`.
- Anything that stages or writes a register → `test_pending_writes.py` or a
  new test for that control.
- Polling, failure handling, new poll keys → `test_coordinator.py`.
- If you add a register that identifies the hardware (a serial number, an
  ID), add it to `SERIAL_REGISTERS` in `diagnostics.py` and to the redaction
  test.

Tests cover the logic, not the radio. Proxy contention, ESPHome changes,
signal strength and firmware behaviour still need testing on real hardware.

## Changes that write to the inverter or batteries

This integration writes directly to the inverter over Bluetooth, so changes
that add or alter writes are held to a higher bar:

1. **A test** covering the new control: the values it stages, any
   validation or range limits, and how it goes through Confirm Write.
2. **A confirmed write on real hardware**, with read-back. In the PR, say
   which inverter or battery model you tested on, the values written, and
   what the register and the vendor app showed afterwards.
3. **Safe defaults.** New controls that could change how the system behaves
   (on/off, heating, recovery, clock) should start disabled by default or
   be marked experimental until more than one system has confirmed them.

Use the `hanchu_ess_ble.read_registers` service (Developer tools → Actions)
to look at a register before proposing a write to it. It's read-only and
never creates entities.

## Testing against your own Home Assistant

`tools/deploy-ha.sh` copies the integration to a Home Assistant host over
SFTP (e.g. via the Advanced SSH & Web Terminal add-on), taking a timestamped
backup of the current copy first:

```bash
export HA_HOST=192.168.1.50          # or pass --host; default homeassistant.local
bash tools/deploy-ha.sh              # backup to .ha-deploy-backups/, then upload
bash tools/deploy-ha.sh --skip-backup
```

Restart Home Assistant afterwards. To roll back:

```bash
bash tools/restore-ha.sh --restore-from .ha-deploy-backups/<timestamp>.tar.gz
```

`tools/backup-ha.sh` takes a backup on its own. All three accept `--user`,
`--port`, `--remote-dir`, and `--password` (or `HANCHU_BLE_SFTP_PASSWORD`,
which needs `sshpass`); run with `--help` for details.

For BLE changes, leave it running for a while before calling it done:
dropouts and stalls often take hours to show up.

## CI checks

Every push and pull request runs:

- **Tests** (`.github/workflows/tests.yml`) — the pytest suite.
- **Hassfest** (`.github/workflows/hassfest.yml`) — Home Assistant's
  integration validator (manifest, services, translations).
- **Validate** (`.github/workflows/validate.yaml`) — HACS validation.

## Pull requests

1. Branch from `main` and keep the change focused.
2. Add or update tests, and docs (README / `docs/`) if behaviour changes.
3. Add a note under `## [Unreleased]` in `CHANGELOG.md`.
4. Reference the related issue (e.g. `Fixes #24`).
5. Make sure `pytest` passes locally.

## Versioning and releases

The integration follows [Semantic Versioning](https://semver.org/):

- **PATCH** — bug fixes and internal changes.
- **MINOR** — new, backwards-compatible entities, services or options.
- **MAJOR** — breaking changes (renamed/removed entities or services, or
  anything needing user action after upgrading).

Releases are cut by the maintainer: bump `version` in
`custom_components/hanchu_ess_ble/manifest.json`, move the `[Unreleased]`
notes in `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD` heading, merge
to `main`, then tag `vX.Y.Z` and publish a GitHub release from the tag. HACS
offers the new tag to users.
