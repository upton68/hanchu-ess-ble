"""BLE-facing helpers for the Hanchu ESS integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import random
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from .const import (
    BLE_NOTIFY_CHARACTERISTIC_UUID,
    BLE_WRITE_CHARACTERISTIC_UUID,
)
from .protocol import (
    AES_ACK_PREFIX,
    HanchuProtocolError,
    HanchuReply,
    HanchuReplyAssembler,
    build_handshake_command,
    build_read_request,
    build_write_request,
    decrypt_message,
    derive_session_key,
    encrypt_message,
)

_LOGGER = logging.getLogger(__name__)
_DEFAULT_OPERATION_TIMEOUT = 10.0
_HANDSHAKE_ACK_TIMEOUT = 5.0
_DEFAULT_READ_BATCH_SIZE = 12
# Overall ceiling for one full connect -> operate -> disconnect cycle.
# Individual steps (connect, handshake, each reply wait) already have their
# own bounded timeouts, but start_notify(), write_gatt_char(), and
# disconnect() do not — some Bleak/BlueZ backends can hang indefinitely on
# these under BLE proxy contention. Wrapping the whole sequence in one outer
# timeout guarantees the coordinator's update always completes or raises
# within a bounded time, regardless of which internal call gets stuck.
_TOTAL_OPERATION_TIMEOUT = 60.0
# Pause before the single retry of a read whose link dropped or went quiet,
# giving the logger and the proxy a moment to release the old connection.
_READ_RETRY_DELAY = 1.0
# Limit for stop_notify + disconnect during cleanup. Cleanup also runs after
# the overall timeout has fired, so it needs its own bound or a hung
# disconnect there would block forever.
_CLOSE_TIMEOUT = 10.0

# Placed on the notification queue when the BLE link drops, so a reply wait
# fails straight away instead of sitting out its full timeout.
_LINK_LOST = object()


class HanchuLinkLostError(BleakError):
    """The BLE link dropped in the middle of an operation."""


class HanchuReplyTimeoutError(TimeoutError):
    """The logger sent no reply to a request in time (link still up)."""


class HanchuOperationTimeoutError(TimeoutError):
    """A whole connect/operate/disconnect cycle exceeded its time limit."""


@dataclass(slots=True)
class HanchuBleStats:
    """Running counts of BLE problems, shown in diagnostics."""

    link_drops: int = 0
    reply_timeouts: int = 0
    operation_timeouts: int = 0
    read_retries: int = 0
    read_retries_succeeded: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class HanchuBleSnapshot:
    """State derived from the latest BLE advertisement."""

    address: str
    name: str | None
    rssi: int | None
    connectable: bool
    last_seen: datetime
    manufacturer_data: dict[int, bytes]
    service_data: dict[str, bytes]


def snapshot_from_service_info(
    service_info: BluetoothServiceInfoBleak,
) -> HanchuBleSnapshot:
    """Convert Home Assistant Bluetooth data into our internal snapshot."""

    return HanchuBleSnapshot(
        address=service_info.address,
        name=service_info.name,
        rssi=service_info.rssi,
        connectable=service_info.connectable,
        last_seen=service_info.time,
        manufacturer_data=dict(service_info.manufacturer_data),
        service_data=dict(service_info.service_data),
    )


class HanchuBleSession:
    """Session-scoped helpers for encrypted Hanchu BLE traffic."""

    def __init__(self) -> None:
        """Initialise a session without an active temporary key."""
        self._secret_key: str | None = None
        self._reply_assembler = HanchuReplyAssembler()

    @property
    def secret_key(self) -> str | None:
        """Return the current session key."""
        return self._secret_key

    def reset(self) -> None:
        """Clear the temporary key and any buffered reply fragments."""
        self._secret_key = None
        self._reply_assembler.reset()

    def build_handshake(self) -> tuple[str, bytes]:
        """Generate a six-character token and its wire-format handshake packet."""
        token = "".join(random.sample("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", 6))
        self._secret_key = derive_session_key(token)
        _LOGGER.debug(
            "Prepared Hanchu session key from token=%s derived_key=%s",
            token,
            self._secret_key,
        )
        return token, build_handshake_command(token)

    def encode_read_request(self, keys: list[str], *, encrypt: bool = True) -> bytes:
        """Encode a JSON read request, encrypting it when requested."""
        payload = build_read_request(keys)
        if not encrypt:
            return payload
        return encrypt_message(payload, self._secret_key)

    def encode_write_request(self, key: str, value, *, encrypt: bool = True) -> bytes:
        """Encode a JSON write request, encrypting it when requested."""
        payload = build_write_request(key, value)
        if not encrypt:
            return payload
        return encrypt_message(payload, self._secret_key)

    def decode_notification(self, payload: bytes, *, encrypted: bool = True) -> HanchuReply | None:
        """Decode a notification payload into a structured reply when complete."""
        packet = decrypt_message(payload, self._secret_key) if encrypted else payload
        return self._reply_assembler.feed(packet)


class HanchuBleClient:
    """Thin BLE client for Hanchu inverter polling."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        """Initialise the client."""
        self.hass = hass
        self.address = address
        self.name = name
        self.stats = HanchuBleStats()
        self._session = HanchuBleSession()
        self._notification_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._connection_lock = asyncio.Lock()
        # Identifies the current operation, so a disconnect callback that
        # arrives late from an earlier operation can't fail the next one.
        self._operation_id = 0

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def async_read_values(
        self,
        keys: list[str],
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Connect, read values, then disconnect.

        If the link drops or the logger stops replying part way through,
        the read is retried once straight away in a fresh connection. The
        logger occasionally drops the link mid-read (seen in debug logs:
        a reply goes missing on the third or fourth batch, and the
        characteristic has vanished by the time notifications are
        stopped), and the next attempt nearly always succeeds. Reads are
        safe to repeat; writes are never retried.

        A whole-operation timeout (a genuine hang) is not retried.
        """
        async with self._connection_lock:
            try:
                return await self._perform_with_timeout(
                    self._async_read_values_inner(keys, encrypted=encrypted)
                )
            except (HanchuLinkLostError, HanchuReplyTimeoutError) as err:
                self.stats.read_retries += 1
                _LOGGER.debug(
                    "Hanchu BLE read for address=%s failed (%s); retrying once",
                    self.address,
                    err,
                )
            await asyncio.sleep(_READ_RETRY_DELAY)
            reply = await self._perform_with_timeout(
                self._async_read_values_inner(keys, encrypted=encrypted)
            )
            self.stats.read_retries_succeeded += 1
            _LOGGER.debug("Hanchu BLE read retry succeeded for address=%s", self.address)
            return reply

    async def async_write_value(
        self,
        key: str,
        value,
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Connect to the inverter, write a single value, then disconnect."""
        async with self._connection_lock:
            return await self._perform_with_timeout(
                self._async_write_value_inner(key, value, encrypted=encrypted)
            )

    async def async_write_values(
        self,
        pairs: list[tuple[str, Any]],
        *,
        encrypted: bool = True,
    ) -> list[HanchuReply]:
        """Connect once, write multiple keys sequentially, then disconnect.

        Not a single atomic device-level write — each pair is still its own
        request/reply round trip, so there's a brief window where an earlier
        key has been applied and a later one hasn't. What this DOES fix is
        the connection-contention cost of a separate connect/disconnect per
        key: everything happens inside one BLE session.

        Uses the same single-key encode_write_request path as
        async_write_value (proven reliable) rather than a multi-entry
        envelope — bench testing showed the firmware reports false success
        for multi-entry writes without actually committing the values, so
        that approach was abandoned in favour of this one.

        Raises on the first non-zero status code rather than continuing,
        so a partial failure is visible instead of silently swallowed.
        Never retried automatically.
        """
        async with self._connection_lock:
            return await self._perform_with_timeout(
                self._async_write_values_inner(pairs, encrypted=encrypted)
            )

    # ------------------------------------------------------------------
    # Operation bodies — always called via _perform_with_timeout
    # ------------------------------------------------------------------

    async def _async_read_values_inner(
        self,
        keys: list[str],
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Read keys in batches over one connection."""
        _LOGGER.debug(
            "Starting Hanchu BLE read address=%s keys=%s encrypted=%s",
            self.address,
            keys,
            encrypted,
        )
        batches = _batched(keys, _DEFAULT_READ_BATCH_SIZE)
        client = await self._async_open(encrypted=encrypted)
        try:
            replies: list[HanchuReply] = []
            for number, batch in enumerate(batches, start=1):
                payload = self._session.encode_read_request(batch, encrypt=encrypted)
                _LOGGER.debug(
                    "Writing Hanchu read request address=%s batch=%d/%d keys=%s payload=%s",
                    self.address,
                    number,
                    len(batches),
                    batch,
                    payload.hex(),
                )
                await client.write_gatt_char(
                    BLE_WRITE_CHARACTERISTIC_UUID,
                    payload,
                    response=False,
                )
                replies.append(
                    await self._async_wait_for_reply(
                        encrypted=encrypted,
                        step=f"read batch {number}/{len(batches)}",
                    )
                )

            reply = _merge_replies(replies)
            _LOGGER.debug(
                "Completed Hanchu BLE read address=%s tid=%s values=%s",
                self.address,
                reply.tid,
                reply.as_dict(),
            )
            return reply
        finally:
            await self._async_close(client)

    async def _async_write_value_inner(
        self,
        key: str,
        value,
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Write one key."""
        _LOGGER.debug(
            "Starting Hanchu BLE write address=%s key=%s value=%s encrypted=%s",
            self.address,
            key,
            value,
            encrypted,
        )
        client = await self._async_open(encrypted=encrypted)
        try:
            payload = self._session.encode_write_request(key, value, encrypt=encrypted)
            _LOGGER.debug(
                "Writing Hanchu write request address=%s key=%s value=%s payload=%s",
                self.address,
                key,
                value,
                payload.hex(),
            )
            await client.write_gatt_char(
                BLE_WRITE_CHARACTERISTIC_UUID,
                payload,
                response=False,
            )
            reply = await self._async_wait_for_reply(
                encrypted=encrypted, step=f"write {key}"
            )
            _LOGGER.debug(
                "Completed Hanchu BLE write address=%s tid=%s reply=%s",
                self.address,
                reply.tid,
                reply.as_dict(),
            )
            return reply
        finally:
            await self._async_close(client)

    async def _async_write_values_inner(
        self,
        pairs: list[tuple[str, Any]],
        *,
        encrypted: bool = True,
    ) -> list[HanchuReply]:
        """Write several keys sequentially over one connection."""
        _LOGGER.debug(
            "Starting Hanchu BLE multi-value write address=%s pairs=%s",
            self.address,
            pairs,
        )
        client = await self._async_open(encrypted=encrypted)
        try:
            replies: list[HanchuReply] = []
            for key, value in pairs:
                payload = self._session.encode_write_request(key, value, encrypt=encrypted)
                _LOGGER.debug(
                    "Writing Hanchu request (sequential multi-value) address=%s key=%s value=%s",
                    self.address,
                    key,
                    value,
                )
                await client.write_gatt_char(
                    BLE_WRITE_CHARACTERISTIC_UUID, payload, response=False
                )
                reply = await self._async_wait_for_reply(
                    encrypted=encrypted, step=f"write {key}"
                )

                status = reply.as_dict().get(key)
                if status != 0:
                    raise HanchuProtocolError(
                        f"Write for key={key} value={value} did not confirm "
                        f"success (status={status}); stopping before remaining "
                        f"{len(pairs) - len(replies) - 1} pair(s)"
                    )
                replies.append(reply)

            _LOGGER.debug(
                "Completed Hanchu BLE multi-value write address=%s all confirmed",
                self.address,
            )
            return replies
        finally:
            await self._async_close(client)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _perform_with_timeout(self, coro):
        """Run a connect/operate/disconnect coroutine under one overall timeout.

        If the wrapped coroutine hangs anywhere — connection establishment,
        notify setup, a GATT write, or disconnect — this raises
        HanchuOperationTimeoutError after _TOTAL_OPERATION_TIMEOUT seconds
        instead of blocking forever, so the coordinator treats it as a
        normal failed cycle.

        Only the overall limit is reported as "exceeded"; a reply timeout or
        dropped link inside the operation propagates unchanged with its own
        message (previously every TimeoutError was logged as a 60s hang).
        """
        try:
            async with asyncio.timeout(_TOTAL_OPERATION_TIMEOUT) as limit:
                return await coro
        except TimeoutError as err:
            if limit.expired():
                self.stats.operation_timeouts += 1
                message = (
                    f"BLE operation exceeded {_TOTAL_OPERATION_TIMEOUT:.0f}s and "
                    "was aborted (hung connect/notify/write/disconnect)"
                )
                self.stats.last_error = message
                _LOGGER.warning("Hanchu %s for address=%s", message, self.address)
                raise HanchuOperationTimeoutError(message) from err
            self.stats.last_error = str(err)
            raise
        except HanchuLinkLostError as err:
            self.stats.last_error = str(err)
            raise

    async def _async_open(self, *, encrypted: bool) -> BleakClient:
        """Connect, start notifications and (if encrypted) do the handshake.

        Disconnects again if any of that fails, so a half-open connection
        is never left behind.
        """
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address,
            connectable=True,
        )
        if ble_device is None:
            raise BleakError(f"No connectable BLE device found for {self.address}")

        self._operation_id += 1
        operation_id = self._operation_id
        self._session.reset()
        self._drain_notifications()
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.name,
            disconnected_callback=lambda _client: self._handle_disconnect(operation_id),
            max_attempts=3,
        )
        _LOGGER.debug("Connected to Hanchu inverter address=%s", self.address)
        try:
            await self._async_start_notify(client)
            if encrypted:
                await self._async_perform_handshake(client)
        except BaseException:
            await self._async_close(client)
            raise
        return client

    async def _async_close(self, client: BleakClient) -> None:
        """Stop notifications (if still connected) and disconnect."""
        # Ignore our own disconnect: the operation is finishing anyway.
        self._operation_id += 1
        try:
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                if client.is_connected:
                    await self._async_stop_notify(client)
                await client.disconnect()
        except TimeoutError:
            _LOGGER.warning(
                "Hanchu BLE disconnect for address=%s did not finish within %.0fs",
                self.address,
                _CLOSE_TIMEOUT,
            )
            return
        _LOGGER.debug("Disconnected from Hanchu inverter address=%s", self.address)

    def _handle_disconnect(self, operation_id: int) -> None:
        """Wake any pending reply wait when the link drops mid-operation."""
        if operation_id != self._operation_id:
            return
        _LOGGER.debug("Hanchu BLE link dropped for address=%s", self.address)
        self._notification_queue.put_nowait(_LINK_LOST)

    async def _async_start_notify(self, client: BleakClient) -> None:
        """Start notifications on the inverter read characteristic."""
        _LOGGER.debug(
            "Starting notifications address=%s characteristic=%s",
            self.address,
            BLE_NOTIFY_CHARACTERISTIC_UUID,
        )
        await client.start_notify(
            BLE_NOTIFY_CHARACTERISTIC_UUID,
            self._notification_handler,
        )

    async def _async_stop_notify(self, client: BleakClient) -> None:
        """Stop notifications if possible."""
        try:
            await client.stop_notify(BLE_NOTIFY_CHARACTERISTIC_UUID)
        except Exception:
            _LOGGER.debug("Failed to stop notifications for %s", self.address, exc_info=True)

    async def _async_next_notification(self, timeout: float, step: str) -> bytes:
        """Return the next notification, or raise if the link drops or it times out."""
        try:
            notification = await asyncio.wait_for(
                self._notification_queue.get(), timeout=timeout
            )
        except TimeoutError as err:
            self.stats.reply_timeouts += 1
            raise HanchuReplyTimeoutError(
                f"No reply to {step} within {timeout:.0f}s"
            ) from err
        if notification is _LINK_LOST:
            self.stats.link_drops += 1
            raise HanchuLinkLostError(f"BLE link dropped during {step}")
        return notification

    async def _async_perform_handshake(self, client: BleakClient) -> None:
        """Send the AES handshake packet and wait for the ack."""
        token, payload = self._session.build_handshake()
        _LOGGER.debug(
            "Sending Hanchu handshake address=%s token=%s payload=%s",
            self.address,
            token,
            payload.hex(),
        )
        await client.write_gatt_char(
            BLE_WRITE_CHARACTERISTIC_UUID,
            payload,
            response=False,
        )

        while True:
            notification = await self._async_next_notification(
                _HANDSHAKE_ACK_TIMEOUT, "handshake"
            )
            _LOGGER.debug(
                "Received handshake-phase notification address=%s payload=%s",
                self.address,
                notification.hex(),
            )
            if notification.startswith(AES_ACK_PREFIX):
                _LOGGER.debug("Handshake acknowledged for address=%s", self.address)
                return

    async def _async_wait_for_reply(self, *, encrypted: bool, step: str) -> HanchuReply:
        """Wait for a full inverter reply to arrive via notifications."""
        while True:
            notification = await self._async_next_notification(
                _DEFAULT_OPERATION_TIMEOUT, step
            )
            _LOGGER.debug(
                "Received Hanchu notification address=%s payload=%s",
                self.address,
                notification.hex(),
            )
            if notification.startswith(AES_ACK_PREFIX):
                continue

            reply = self._session.decode_notification(notification, encrypted=encrypted)
            if reply is not None:
                return reply

    def _notification_handler(
        self,
        _characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Buffer notification bytes for the active request."""
        _LOGGER.debug(
            "Notification callback address=%s bytes=%s",
            self.address,
            bytes(data).hex(),
        )
        self._notification_queue.put_nowait(bytes(data))

    def _drain_notifications(self) -> None:
        """Discard any notifications left from a previous operation."""
        while not self._notification_queue.empty():
            self._notification_queue.get_nowait()


def _batched(values: list[str], size: int) -> list[list[str]]:
    """Return values split into bounded batches."""

    return [values[index : index + size] for index in range(0, len(values), size)]


def _merge_replies(replies: list[HanchuReply]) -> HanchuReply:
    """Merge batched read replies into one logical response."""

    if not replies:
        return HanchuReply(tid=None, act=None, cmd=None, code=None)

    first = replies[0]
    data = [point for reply in replies for point in reply.data]
    return HanchuReply(
        tid=first.tid,
        act=first.act,
        cmd=first.cmd,
        code=first.code,
        data=data,
    )
