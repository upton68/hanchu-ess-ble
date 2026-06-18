"""BLE-facing helpers for the Hanchu ESS integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import random

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
        self._session = HanchuBleSession()
        self._notification_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._connection_lock = asyncio.Lock()

    async def async_read_values(
        self,
        keys: list[str],
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Connect to the inverter, read values, then disconnect."""
        async with self._connection_lock:
            return await self._async_read_values_locked(keys, encrypted=encrypted)

    async def _async_read_values_locked(
        self,
        keys: list[str],
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Body of async_read_values, run while holding the connection lock."""
        _LOGGER.debug(
            "Starting Hanchu BLE read address=%s keys=%s encrypted=%s",
            self.address,
            keys,
            encrypted,
        )
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address,
            connectable=True,
        )
        if ble_device is None:
            raise BleakError(f"No connectable BLE device found for {self.address}")

        self._session.reset()
        self._drain_notifications()
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.name,
            max_attempts=3,
        )
        try:
            _LOGGER.debug("Connected to Hanchu inverter address=%s", self.address)
            await self._async_start_notify(client)
            if encrypted:
                await self._async_perform_handshake(client)

            replies: list[HanchuReply] = []
            for batch in _batched(keys, _DEFAULT_READ_BATCH_SIZE):
                payload = self._session.encode_read_request(batch, encrypt=encrypted)
                _LOGGER.debug(
                    "Writing Hanchu read request address=%s keys=%s payload=%s",
                    self.address,
                    batch,
                    payload.hex(),
                )
                await client.write_gatt_char(
                    BLE_WRITE_CHARACTERISTIC_UUID,
                    payload,
                    response=False,
                )
                replies.append(await self._async_wait_for_reply(encrypted=encrypted))

            reply = _merge_replies(replies)
            _LOGGER.debug(
                "Completed Hanchu BLE read address=%s tid=%s values=%s",
                self.address,
                reply.tid,
                reply.as_dict(),
            )
            return reply
        finally:
            await self._async_stop_notify(client)
            await client.disconnect()
            _LOGGER.debug("Disconnected from Hanchu inverter address=%s", self.address)

    async def async_write_value(
        self,
        key: str,
        value,
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Connect to the inverter, write a single value, then disconnect."""
        async with self._connection_lock:
            return await self._async_write_value_locked(key, value, encrypted=encrypted)

    async def _async_write_value_locked(
        self,
        key: str,
        value,
        *,
        encrypted: bool = True,
    ) -> HanchuReply:
        """Body of async_write_value, run while holding the connection lock."""
        _LOGGER.debug(
            "Starting Hanchu BLE write address=%s key=%s value=%s encrypted=%s",
            self.address,
            key,
            value,
            encrypted,
        )
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address,
            connectable=True,
        )
        if ble_device is None:
            raise BleakError(f"No connectable BLE device found for {self.address}")

        self._session.reset()
        self._drain_notifications()
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.name,
            max_attempts=3,
        )
        try:
            _LOGGER.debug("Connected to Hanchu inverter address=%s", self.address)
            await self._async_start_notify(client)
            if encrypted:
                await self._async_perform_handshake(client)

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
            reply = await self._async_wait_for_reply(encrypted=encrypted)
            _LOGGER.debug(
                "Completed Hanchu BLE write address=%s tid=%s reply=%s",
                self.address,
                reply.tid,
                reply.as_dict(),
            )
            return reply
        finally:
            await self._async_stop_notify(client)
            await client.disconnect()
            _LOGGER.debug("Disconnected from Hanchu inverter address=%s", self.address)

    

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
            notification = await asyncio.wait_for(
                self._notification_queue.get(),
                timeout=_HANDSHAKE_ACK_TIMEOUT,
            )
            _LOGGER.debug(
                "Received handshake-phase notification address=%s payload=%s",
                self.address,
                notification.hex(),
            )
            if notification.startswith(AES_ACK_PREFIX):
                _LOGGER.debug("Handshake acknowledged for address=%s", self.address)
                return

    async def _async_wait_for_reply(self, *, encrypted: bool) -> HanchuReply:
        """Wait for a full inverter reply to arrive via notifications."""
        while True:
            notification = await asyncio.wait_for(
                self._notification_queue.get(),
                timeout=_DEFAULT_OPERATION_TIMEOUT,
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
