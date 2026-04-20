"""Protocol helpers for Hanchu inverter BLE communication."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any

from Crypto.Cipher import AES

_LOGGER = logging.getLogger(__name__)

_READ_MESSAGE_TYPE = 0x03
_AES_HANDSHAKE_PREFIX = 0x05
_AES_HANDSHAKE_LENGTH = 6
_FINAL_PACKET_TYPE = 0
_FRAME_HEADER_LENGTH = 6
AES_ACK_PREFIX = b"\x05\x00"


class HanchuProtocolError(ValueError):
    """Raised when a Hanchu BLE frame is malformed."""


def _codec_basis() -> bytes:
    """Return the static protocol basis used for local BLE sessions."""

    segments = (
        bytes((0x67, 0x78)),
        bytes((0x6B, 0x6A, 0x40, 0x32)),
        bytes((0x30, 0x39, 0x39, 0x40)),
        bytes((0x31, 0x39, 0x31, 0x34)),
        bytes((0x7A, 0x79)),
    )
    return b"".join(segments)


def _state_vector() -> bytes:
    """Return the static state vector used by the local BLE codec."""

    values = (
        0x39,
        0x7A,
        0x36,
        0x34,
        0x51,
        0x72,
        0x38,
        0x6D,
        0x5A,
        0x48,
        0x37,
        0x50,
        0x67,
        0x38,
        0x64,
        0x31,
    )
    return bytes(values)


def _hex_preview(payload: bytes, limit: int = 64) -> str:
    """Return a short hex preview for debug logging."""

    hex_payload = payload.hex()
    if len(hex_payload) <= limit:
        return hex_payload
    return f"{hex_payload[:limit]}..."


@dataclass(slots=True)
class HanchuDataPoint:
    """A single inverter key/value pair."""

    key: str
    value: Any = None


@dataclass(slots=True)
class HanchuReply:
    """Decoded inverter reply payload."""

    tid: str | None
    act: str | None
    cmd: str | None
    code: str | None
    data: list[HanchuDataPoint] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the data payload as a key/value map."""
        return {point.key: point.value for point in self.data}


def derive_session_key(handshake_token: str) -> str:
    """Derive the temporary AES key from a six-character handshake token."""

    if len(handshake_token) != _AES_HANDSHAKE_LENGTH:
        raise HanchuProtocolError("Handshake token must be exactly 6 characters")

    try:
        start_index = ord(handshake_token[5]) % 10
        key_chars = list(_codec_basis().decode("utf-8"))
        for index, char in enumerate(handshake_token):
            key_chars[start_index + index] = char
    except IndexError as err:
        raise HanchuProtocolError("Failed to derive session key from handshake token") from err

    return "".join(key_chars)


def build_handshake_command(handshake_token: str) -> bytes:
    """Build the initial unencrypted AES handshake packet."""

    if len(handshake_token) != _AES_HANDSHAKE_LENGTH:
        raise HanchuProtocolError("Handshake token must be exactly 6 characters")

    command = bytes([_AES_HANDSHAKE_PREFIX]) + handshake_token.encode("ascii")
    _LOGGER.debug(
        "Built Hanchu handshake command token=%s payload=%s",
        handshake_token,
        _hex_preview(command),
    )
    return command


def encrypt_message(payload: bytes, secret_key: str | None = None) -> bytes:
    """Encrypt a Hanchu payload with AES/CFB8/NoPadding."""

    key = secret_key.encode("utf-8") if secret_key is not None else _codec_basis()
    cipher = AES.new(key, AES.MODE_CFB, iv=_state_vector(), segment_size=8)
    encrypted = cipher.encrypt(payload)
    _LOGGER.debug(
        "Encrypted Hanchu payload plaintext=%s ciphertext=%s",
        _hex_preview(payload),
        _hex_preview(encrypted),
    )
    return encrypted


def decrypt_message(payload: bytes, secret_key: str | None = None) -> bytes:
    """Decrypt a Hanchu payload with AES/CFB8/NoPadding."""

    key = secret_key.encode("utf-8") if secret_key is not None else _codec_basis()
    cipher = AES.new(key, AES.MODE_CFB, iv=_state_vector(), segment_size=8)
    decrypted = cipher.decrypt(payload)
    _LOGGER.debug(
        "Decrypted Hanchu payload ciphertext=%s plaintext=%s",
        _hex_preview(payload),
        _hex_preview(decrypted),
    )
    return decrypted


def build_read_request(keys: list[str], tid: str = "10001") -> bytes:
    """Build a compact JSON read request for inverter keys."""

    payload = {
        "act": "1",
        "cmd": "local",
        "data": [{"k": key} for key in keys],
        "tid": tid,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    _LOGGER.debug("Built Hanchu read request tid=%s keys=%s payload=%s", tid, keys, encoded)
    return encoded


def parse_reply_payload(payload: bytes) -> HanchuReply:
    """Parse a JSON reply payload into a structured object."""

    cleaned_payload = payload.rstrip(b"\x00").decode("utf-8")
    _LOGGER.debug("Parsing Hanchu reply payload=%s", cleaned_payload)
    document = json.loads(cleaned_payload)
    points = [
        HanchuDataPoint(key=item["k"], value=item.get("v"))
        for item in document.get("data", [])
        if "k" in item
    ]
    return HanchuReply(
        tid=document.get("tid"),
        act=document.get("act"),
        cmd=document.get("cmd"),
        code=document.get("code"),
        data=points,
    )


@dataclass(slots=True)
class HanchuPacket:
    """A single framed BLE notification packet."""

    packet_type: int
    packet_index: int
    payload_length: int
    payload: bytes


def parse_packet(packet: bytes) -> HanchuPacket | None:
    """Parse a single notification packet.

    Returns `None` for the short `0500`-style AES acknowledgement frames that the
    Android app explicitly ignores.
    """

    if packet.startswith(AES_ACK_PREFIX) and len(packet) <= _FRAME_HEADER_LENGTH:
        _LOGGER.debug("Ignoring short Hanchu AES ack packet=%s", _hex_preview(packet))
        return None

    if len(packet) < _FRAME_HEADER_LENGTH:
        raise HanchuProtocolError("Notification packet is too short")

    if packet[0] != _READ_MESSAGE_TYPE:
        raise HanchuProtocolError(f"Unsupported notification type 0x{packet[0]:02x}")

    payload_length = int.from_bytes(packet[4:6], byteorder="little")
    payload = packet[6 : 6 + payload_length]
    if len(payload) != payload_length:
        raise HanchuProtocolError("Notification packet payload length mismatch")

    return HanchuPacket(
        packet_type=packet[1],
        packet_index=int.from_bytes(packet[2:4], byteorder="little"),
        payload_length=payload_length,
        payload=payload,
    )


class HanchuReplyAssembler:
    """Reassemble one or more BLE reply packets into a decoded response."""

    def __init__(self) -> None:
        """Initialise an empty packet buffer."""
        self._parts: dict[int, bytes] = {}

    def reset(self) -> None:
        """Clear any partially assembled response."""
        self._parts.clear()

    def feed(self, packet: bytes) -> HanchuReply | None:
        """Feed a decrypted notification packet into the assembler."""

        parsed = parse_packet(packet)
        if parsed is None:
            return None

        self._parts[parsed.packet_index] = parsed.payload
        _LOGGER.debug(
            "Received Hanchu packet type=%s index=%s payload_length=%s",
            parsed.packet_type,
            parsed.packet_index,
            parsed.payload_length,
        )

        if parsed.packet_type != _FINAL_PACKET_TYPE:
            return None

        merged = b"".join(payload for _, payload in sorted(self._parts.items()))
        _LOGGER.debug(
            "Assembled Hanchu reply from %s packet(s): %s",
            len(self._parts),
            _hex_preview(merged, limit=128),
        )
        self.reset()
        return parse_reply_payload(merged)
