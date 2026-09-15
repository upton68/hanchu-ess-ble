"""Staged pending-writes buffer for hanchu-ess-ble manual (UI) edits.

One instance per device (inverter), created in async_setup_entry and
stored in hass.data[DOMAIN][entry_id]["pending_writes"] so every
number/select entity and both buttons can share it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .protocol import HanchuProtocolError

_LOGGER = logging.getLogger(__name__)

# Auto-discard a staged-but-unconfirmed edit after this long.
PENDING_TIMEOUT_SECONDS = 300


@dataclass
class PendingWriteBuffer:
    hass: HomeAssistant
    ble_client: Any  # HanchuBleClient instance for this device
    _pending: dict[str, Any] = field(default_factory=dict)
    _cancel_timeout: Callable[[], None] | None = field(default=None, init=False)
    _listeners: list[Callable[[], None]] = field(default_factory=list)
    _in_progress: bool = field(default=False, init=False)

    def stage(self, register_key: str, value: Any) -> None:
        """Called by a number/select entity instead of writing to BLE."""
        self._pending[register_key] = value
        self._reset_timeout()
        self._notify_listeners()

    def is_pending(self, register_key: str) -> bool:
        return register_key in self._pending

    def discard(self) -> None:
        if not self._pending:
            return
        if self._in_progress:
            _LOGGER.warning(
                "Cannot discard while a confirm is in progress; ignoring"
            )
            return
        self._pending.clear()
        self._cancel_pending_timeout()
        self._notify_listeners()

    @property
    def is_confirming(self) -> bool:
        """True while a confirm() is actively in flight.

        Guards against a second Confirm press launching another
        async_write_values call while an earlier one is still running (e.g.
        stuck in a BLE hang) — without this, presses just queue silently on
        the client's connection lock and pile up rather than being visibly
        rejected, which is what happened during testing tonight.
        """
        return self._in_progress

    async def confirm(self) -> None:
        """Flush everything staged in one BLE connection (sequential writes).

        Uses async_write_values, which opens one connection and writes each
        staged key/value pair in turn before disconnecting once. This is
        NOT a single atomic device-level write — the multi-entry envelope
        approach (build_multi_write_request) was bench-tested and found to
        report false success without actually committing values, so it was
        abandoned. async_write_values reuses the proven single-key write
        path instead, just inside one shared connection.

        If any pair fails to confirm (TimeoutError, or a non-zero status
        code raised as HanchuProtocolError), the buffer is left intact so
        the failed edit(s) can be retried rather than silently lost.

        A second call while one is already in flight is rejected outright
        (see is_confirming) rather than being allowed to queue.
        """
        if not self._pending:
            return
        if self._in_progress:
            _LOGGER.warning(
                "Confirm already in progress; ignoring duplicate request"
            )
            return

        self._in_progress = True
        self._notify_listeners()  # let the button go unavailable immediately
        try:
            pairs = list(self._pending.items())
            await self.ble_client.async_write_values(pairs)
        except (TimeoutError, HanchuProtocolError):
            _LOGGER.warning(
                "Failed to confirm %d staged register write(s); "
                "changes remain staged for retry",
                len(self._pending),
            )
            raise
        else:
            self._pending.clear()
            self._cancel_pending_timeout()
        finally:
            self._in_progress = False
            self._notify_listeners()

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def add_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()

    def _reset_timeout(self) -> None:
        self._cancel_pending_timeout()
        self._cancel_timeout = async_call_later(
            self.hass, PENDING_TIMEOUT_SECONDS, self._handle_timeout
        )

    @callback
    def _handle_timeout(self, _now) -> None:
        """Auto-discard once the pending timeout elapses.

        Must be marked @callback so Home Assistant runs it directly on the
        event loop. Without this, HA treats a plain function as unsafe to
        run on the loop and may dispatch it to a worker thread instead —
        and discard() -> _notify_listeners() -> entity.async_write_ha_state()
        requires the event loop thread, so it can fail silently there rather
        than doing anything visible. That looked exactly like "the timeout
        should have fired but nothing changed."
        """
        self.discard()

    def _cancel_pending_timeout(self) -> None:
        if self._cancel_timeout is not None:
            self._cancel_timeout()
            self._cancel_timeout = None
