"""A fake Hanchu logger that speaks the real BLE wire protocol.

Used to drive HanchuBleClient end to end without Bluetooth hardware:
handshake -> session key -> encrypted JSON request -> framed, encrypted,
multi-packet reply. Only the transport (Bleak) is faked; every byte the
client sends and receives goes through the integration's own protocol code.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.hanchu_ess_ble.protocol import (
    decrypt_message,
    derive_session_key,
    encrypt_message,
)

ACK = b"\x05\x00"


def frame_reply(document: dict[str, Any], chunk_size: int = 40) -> list[bytes]:
    """Split a JSON reply into Hanchu notification frames (unencrypted).

    Frame layout: [0x03, type, index LE16, length LE16, payload]; type 0
    marks the final frame, anything else means more follow.
    """
    body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    frames = []
    for index, chunk in enumerate(chunks):
        packet_type = 0 if index == len(chunks) - 1 else 1
        frames.append(
            bytes([0x03, packet_type])
            + index.to_bytes(2, "little")
            + len(chunk).to_bytes(2, "little")
            + chunk
        )
    return frames


class FakeLogger:
    """Device side of the protocol: registers plus request handling."""

    def __init__(self, registers: dict[str, Any] | None = None) -> None:
        self.registers: dict[str, Any] = dict(registers or {})
        # Per-key write status; anything other than 0 is a rejected write.
        self.write_status: dict[str, int] = {}
        self.session_key: str | None = None
        self.read_requests: list[list[str]] = []
        self.write_requests: list[tuple[str, Any]] = []
        self.chunk_size = 40

    def handle(self, payload: bytes) -> list[bytes]:
        """Return the notifications the logger sends for one GATT write."""
        if payload[0] == 0x05 and len(payload) == 7:
            self.session_key = derive_session_key(payload[1:].decode("ascii"))
            return [ACK]

        request = json.loads(decrypt_message(payload, self.session_key))
        items = request["data"]
        if request["act"] == "1":
            keys = [item["k"] for item in items]
            self.read_requests.append(keys)
            data = [
                {"k": key, "v": self.registers[key]}
                for key in keys
                if key in self.registers
            ]
        elif request["act"] == "3":
            data = []
            for item in items:
                key, value = item["k"], item["v"]
                self.write_requests.append((key, value))
                status = self.write_status.get(key, 0)
                if status == 0:
                    self.registers[key] = value
                data.append({"k": key, "v": status})
        else:  # pragma: no cover - the client never sends anything else
            raise AssertionError(f"unexpected act {request['act']!r}")

        reply = {
            "act": request["act"],
            "cmd": request["cmd"],
            "code": "0",
            "data": data,
            "tid": request["tid"],
        }
        return [
            encrypt_message(frame, self.session_key)
            for frame in frame_reply(reply, self.chunk_size)
        ]


class FakeBleakClient:
    """Stands in for BleakClientWithServiceCache, wired to a FakeLogger."""

    def __init__(self, logger: FakeLogger) -> None:
        self.logger = logger
        self._handler: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None
        self.is_connected = False
        self.disconnected = False
        self.connections = 0
        self.stop_notify_calls = 0
        # Set to an Event to make the matching call hang until it's set.
        self.hang_on_write: asyncio.Event | None = None
        self.hang_on_disconnect: asyncio.Event | None = None
        # Drop the link on these request writes (1-based count of non-
        # handshake writes per connection), like the real logger sometimes
        # does mid-read. Each entry is consumed once.
        self.drop_link_on_request: list[int] = []
        # Swallow (never answer) these request writes, with the link up.
        self.ignore_request: list[int] = []
        self._requests_this_connection = 0

    def connect(self, disconnected_callback=None) -> "FakeBleakClient":
        """What establish_connection does: (re)connect and remember the callback."""
        self.disconnected_callback = disconnected_callback
        self.is_connected = True
        self.disconnected = False
        self.connections += 1
        self._requests_this_connection = 0
        return self

    def _drop_link(self) -> None:
        self.is_connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)

    async def start_notify(self, _uuid: str, handler) -> None:
        self._handler = handler

    async def stop_notify(self, _uuid: str) -> None:
        if not self.is_connected:
            raise AssertionError("stop_notify on a dropped link (real bleak raises here)")
        self.stop_notify_calls += 1
        self._handler = None

    async def write_gatt_char(self, _uuid: str, payload: bytes, response=False) -> None:
        if self.hang_on_write is not None:
            await self.hang_on_write.wait()
        is_handshake = payload[0] == 0x05 and len(payload) == 7
        if not is_handshake:
            self._requests_this_connection += 1
            n = self._requests_this_connection
            if n in self.drop_link_on_request:
                self.drop_link_on_request.remove(n)
                # Let the caller start waiting for the reply first.
                asyncio.get_running_loop().call_soon(self._drop_link)
                return
            if n in self.ignore_request:
                self.ignore_request.remove(n)
                return
        for notification in self.logger.handle(bytes(payload)):
            assert self._handler is not None, "write before start_notify"
            self._handler(None, bytearray(notification))

    async def disconnect(self) -> None:
        if self.hang_on_disconnect is not None:
            await self.hang_on_disconnect.wait()
        was_connected = self.is_connected
        self.is_connected = False
        self.disconnected = True
        if was_connected and self.disconnected_callback is not None:
            # Real bleak calls the callback for our own disconnects too.
            self.disconnected_callback(self)


@contextmanager
def patched_ble(bleak_client: FakeBleakClient, *, device_found: bool = True):
    """Route HanchuBleClient's connection calls to `bleak_client`."""
    base = "custom_components.hanchu_ess_ble.ble_client"
    connect = AsyncMock(
        side_effect=lambda *_args, disconnected_callback=None, **_kwargs: (
            bleak_client.connect(disconnected_callback)
        )
    )
    with (
        patch(
            f"{base}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock() if device_found else None,
        ),
        patch(f"{base}.establish_connection", connect),
    ):
        yield connect
