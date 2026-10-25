"""Manual clock sync for the inverter's onboard RTC (L094 / L096).

EXPERIMENTAL — not yet tested on real hardware by the maintainer. See issue
#24 (and the earlier discussion in #4).

Registers involved:
- L020: IANA time zone (e.g. "Europe/London"). Read only here.
- L094: RTC as Unix epoch seconds (UTC).
- L096: daylight-saving offset in MINUTES (60 during BST, 0 during GMT,
  confirmed by live reads). Not hours, and treated as the DST component on
  top of L020's zone rather than the full UTC offset. In the UK those are
  identical, so this can't be told apart from a UK system.

Normally the Hanchu cloud keeps these right (L096 went 60 -> 0 on the
maintainer's system overnight on 25 Oct 2026). This exists as a local
fallback for systems that don't get that update, e.g. if cloud access is
lost.

Safeguards:
- Manual only (button disabled by default, or the sync_clock service).
- Reads first and only writes registers that are actually wrong, unless
  forced. Epoch tolerance is CLOCK_DRIFT_TOLERANCE_SECONDS.
- Refuses if the device's L020 zone doesn't match Home Assistant's zone,
  since the DST value would then be computed for the wrong place.
- Refuses while a Confirm Write flush is in progress.
- Reads both registers back afterwards and fails loudly if either doesn't
  match, rather than assuming the write took.
- dry_run reports what would be written without writing anything.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import HanchuBleCoordinator
    from .pending_writes import PendingWriteBuffer

_LOGGER = logging.getLogger(__name__)

TIMEZONE_KEY = "L020"
RTC_EPOCH_KEY = "L094"
DST_OFFSET_KEY = "L096"

# Treat the RTC as correct if it's within this many seconds of HA's clock.
# Allows for BLE round-trip time and the gap between reading and comparing.
CLOCK_DRIFT_TOLERANCE_SECONDS = 60

_LOCKS: dict[str, asyncio.Lock] = {}


class ClockSyncError(Exception):
    """Raised when a clock sync is refused or fails verification."""


def _to_int(raw: Any) -> int | None:
    """Parse a register value (may arrive as str, int or float) to int."""
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def dst_offset_minutes(tz: ZoneInfo, when: datetime | None = None) -> int:
    """Return the DST component of `tz` at `when`, in whole minutes."""
    local = (when or datetime.now(timezone.utc)).astimezone(tz)
    dst = local.dst()
    return int(dst.total_seconds() // 60) if dst else 0


async def async_sync_clock(
    coordinator: HanchuBleCoordinator,
    pending_writes: PendingWriteBuffer | None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Check the inverter clock against Home Assistant and correct it if needed.

    Returns a result dict. Raises ClockSyncError if the sync is refused or
    the read-back doesn't match.
    """
    lock = _LOCKS.setdefault(coordinator.address, asyncio.Lock())
    if lock.locked():
        raise ClockSyncError("A clock sync is already running")

    async with lock:
        if pending_writes is not None and pending_writes.is_confirming:
            raise ClockSyncError(
                "A Confirm Write is in progress; try again once it finishes"
            )

        ha_tz = coordinator.hass.config.time_zone
        client = coordinator.client

        # 1. Read the current state.
        reply = await client.async_read_values(
            [TIMEZONE_KEY, RTC_EPOCH_KEY, DST_OFFSET_KEY], encrypted=True
        )
        values = reply.as_dict()
        read_at = int(time.time())
        device_tz = values.get(TIMEZONE_KEY)
        device_epoch = _to_int(values.get(RTC_EPOCH_KEY))
        device_dst = _to_int(values.get(DST_OFFSET_KEY))

        if device_tz and str(device_tz).strip() != ha_tz:
            raise ClockSyncError(
                f"Inverter time zone ({device_tz}) doesn't match Home "
                f"Assistant's ({ha_tz}); not writing a DST offset for the "
                "wrong zone"
            )

        # HA's own zone object (already loaded, so no blocking file I/O here).
        target_dst = dst_offset_minutes(dt_util.get_default_time_zone())

        drift = None if device_epoch is None else device_epoch - read_at
        dst_wrong = force or device_dst != target_dst
        epoch_wrong = (
            force or drift is None or abs(drift) > CLOCK_DRIFT_TOLERANCE_SECONDS
        )

        result: dict[str, Any] = {
            "time_zone": ha_tz,
            "device_time_zone": device_tz,
            "before": {
                DST_OFFSET_KEY: device_dst,
                RTC_EPOCH_KEY: device_epoch,
                "drift_seconds": drift,
            },
            "target": {DST_OFFSET_KEY: target_dst},
            "dry_run": dry_run,
        }

        # Write order matches the order 1ulk tested: offset first, then epoch.
        planned = []
        if dst_wrong:
            planned.append(DST_OFFSET_KEY)
        if epoch_wrong:
            planned.append(RTC_EPOCH_KEY)
        result["would_write" if dry_run else "written"] = planned

        if not planned:
            result["message"] = "Inverter clock already correct; nothing written"
            return result
        if dry_run:
            result["message"] = f"Dry run: would write {', '.join(planned)}"
            return result

        # 2. Write. Epoch is taken as late as possible so it's fresh.
        pairs: list[tuple[str, Any]] = []
        if dst_wrong:
            pairs.append((DST_OFFSET_KEY, target_dst))
        if epoch_wrong:
            pairs.append((RTC_EPOCH_KEY, int(time.time())))
        _LOGGER.warning("Hanchu clock sync writing %s", pairs)
        await client.async_write_values(pairs, encrypted=True)

        # 3. Read back and verify.
        check = (
            await client.async_read_values(
                [RTC_EPOCH_KEY, DST_OFFSET_KEY], encrypted=True
            )
        ).as_dict()
        checked_at = int(time.time())
        after_epoch = _to_int(check.get(RTC_EPOCH_KEY))
        after_dst = _to_int(check.get(DST_OFFSET_KEY))
        after_drift = None if after_epoch is None else after_epoch - checked_at
        result["after"] = {
            DST_OFFSET_KEY: after_dst,
            RTC_EPOCH_KEY: after_epoch,
            "drift_seconds": after_drift,
        }

        problems = []
        if after_dst != target_dst:
            problems.append(f"L096 reads {after_dst}, expected {target_dst}")
        if after_drift is None or abs(after_drift) > CLOCK_DRIFT_TOLERANCE_SECONDS:
            problems.append(f"L094 is {after_drift}s from Home Assistant's clock")

        await coordinator.async_request_refresh()

        if problems:
            _LOGGER.error("Hanchu clock sync verification failed: %s", "; ".join(problems))
            raise ClockSyncError(
                "Write was acknowledged but read-back didn't match: "
                + "; ".join(problems)
            )

        result["message"] = "Inverter clock synced and verified"
        _LOGGER.warning("Hanchu clock sync verified: %s", result["after"])
        return result
