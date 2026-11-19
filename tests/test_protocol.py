"""Tests for the BLE wire protocol: key derivation, crypto, framing, parsing."""

from __future__ import annotations

import json

import pytest

from custom_components.hanchu_ess_ble.protocol import (
    HanchuProtocolError,
    HanchuReplyAssembler,
    build_handshake_command,
    build_read_request,
    build_write_request,
    decrypt_message,
    derive_session_key,
    encrypt_message,
    parse_packet,
    parse_reply_payload,
)

from .fake_inverter import frame_reply

BASIS = "gxkj@2099@1914zy"


# --- Session key / handshake -------------------------------------------------


def test_derive_session_key_overlays_token_at_offset():
    # ord("3") % 10 == 1, so the token replaces characters 1..6 of the basis.
    assert derive_session_key("ABC123") == "g" + "ABC123" + BASIS[7:]


@pytest.mark.parametrize("last", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
def test_derive_session_key_always_16_chars(last):
    # Every possible start offset (0..9) must still fit inside the basis.
    key = derive_session_key("ABCDE" + last)
    assert len(key) == 16
    start = ord(last) % 10
    assert key[start : start + 6] == "ABCDE" + last


@pytest.mark.parametrize("token", ["", "ABC12", "ABC1234"])
def test_derive_session_key_rejects_wrong_length(token):
    with pytest.raises(HanchuProtocolError):
        derive_session_key(token)


def test_build_handshake_command():
    assert build_handshake_command("ABC123") == b"\x05ABC123"


def test_build_handshake_command_rejects_wrong_length():
    with pytest.raises(HanchuProtocolError):
        build_handshake_command("ABC")


# --- Crypto -------------------------------------------------------------------


@pytest.mark.parametrize("key", [None, derive_session_key("XYZ789")])
def test_encrypt_decrypt_round_trip(key):
    plaintext = b'{"act":"1","cmd":"local","data":[{"k":"P071"}],"tid":"10001"}'
    ciphertext = encrypt_message(plaintext, key)
    assert ciphertext != plaintext
    # CFB8 is a stream mode: no padding, same length.
    assert len(ciphertext) == len(plaintext)
    assert decrypt_message(ciphertext, key) == plaintext


def test_session_key_changes_ciphertext():
    plaintext = b"same payload"
    assert encrypt_message(plaintext, derive_session_key("AAAAA1")) != encrypt_message(
        plaintext, derive_session_key("BBBBB2")
    )


# --- Requests -----------------------------------------------------------------


def test_build_read_request_exact_bytes():
    assert build_read_request(["P071", "P067"]) == (
        b'{"act":"1","cmd":"local","data":[{"k":"P071"},{"k":"P067"}],"tid":"10001"}'
    )


def test_build_write_request_exact_bytes():
    assert build_write_request("P500", 1, tid="42") == (
        b'{"act":"3","cmd":"local","data":[{"k":"P500","v":1}],"tid":"42"}'
    )


# --- Reply parsing ------------------------------------------------------------


def _reply(data, **extra):
    return json.dumps({"tid": "10001", "act": "1", "cmd": "local", "code": "0",
                       "data": data, **extra}).encode()


def test_parse_reply_payload_basic():
    reply = parse_reply_payload(_reply([{"k": "P071", "v": 64}, {"k": "P067", "v": 53.1}]))
    assert reply.tid == "10001"
    assert reply.code == "0"
    assert reply.as_dict() == {"P071": 64, "P067": 53.1}


def test_parse_reply_payload_trims_transport_bytes_and_nulls():
    payload = b"\x03\x00\x01" + _reply([{"k": "P071", "v": 1}]) + b"\x00\x00"
    assert parse_reply_payload(payload).as_dict() == {"P071": 1}


def test_parse_reply_payload_skips_items_without_key():
    reply = parse_reply_payload(_reply([{"v": 1}, {"k": "P071", "v": 2}]))
    assert reply.as_dict() == {"P071": 2}


def test_parse_reply_payload_unprogrammed_memory_becomes_none():
    # Older battery packs return 0xFF bytes for B001; that must not fail the
    # whole reply (v1.7.0 fix), and a value made only of junk becomes None.
    body = b'{"data":[{"k":"B001","v":"\xff\xff\xff"},{"k":"B034","v":22.45}]}'
    assert parse_reply_payload(body).as_dict() == {"B001": None, "B034": 22.45}


def test_parse_reply_payload_strips_junk_around_text():
    body = b'{"data":[{"k":"B002","v":"B0079K\xff\xff"}]}'
    assert parse_reply_payload(body).as_dict() == {"B002": "B0079K"}


def test_parse_reply_payload_non_object_raises():
    with pytest.raises(HanchuProtocolError):
        parse_reply_payload(b"[1, 2, 3]")


def test_parse_reply_payload_without_json_raises():
    with pytest.raises(HanchuProtocolError):
        parse_reply_payload(b"\x01\x02\x03")


def test_parse_reply_payload_broken_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_reply_payload(b'{"data": [')


# --- Packet framing -----------------------------------------------------------


def test_parse_packet_ignores_aes_ack():
    assert parse_packet(b"\x05\x00") is None
    assert parse_packet(b"\x05\x00\x00\x00") is None


def test_parse_packet_ignores_undersized():
    assert parse_packet(b"\x03\x00\x00") is None


def test_parse_packet_ignores_unknown_type():
    assert parse_packet(b"\x09\x00\x00\x00\x01\x00X") is None


def test_parse_packet_ignores_length_mismatch():
    # Header claims 10 bytes, only 3 present.
    assert parse_packet(b"\x03\x00\x00\x00\x0a\x00abc") is None


def test_parse_packet_plain_json_is_a_final_frame():
    packet = parse_packet(b'{"data":[]}')
    assert packet is not None
    assert packet.packet_type == 0
    assert packet.payload == b'{"data":[]}'


def test_parse_packet_binary_frame():
    packet = parse_packet(b"\x03\x01\x02\x00\x03\x00abc")
    assert packet is not None
    assert (packet.packet_type, packet.packet_index, packet.payload) == (1, 2, b"abc")


# --- Reassembly ---------------------------------------------------------------


def _document():
    return {
        "act": "1", "cmd": "local", "code": "0", "tid": "10001",
        "data": [{"k": f"P{n:03d}", "v": n} for n in range(20)],
    }


def test_assembler_single_frame():
    assembler = HanchuReplyAssembler()
    (frame,) = frame_reply({"data": [{"k": "P071", "v": 5}]}, chunk_size=1000)
    assert assembler.feed(frame).as_dict() == {"P071": 5}


def test_assembler_multi_frame_in_order():
    assembler = HanchuReplyAssembler()
    frames = frame_reply(_document(), chunk_size=30)
    assert len(frames) > 3
    for frame in frames[:-1]:
        assert assembler.feed(frame) is None
    reply = assembler.feed(frames[-1])
    assert reply.as_dict() == {f"P{n:03d}": n for n in range(20)}


def test_assembler_reorders_out_of_order_fragments():
    assembler = HanchuReplyAssembler()
    frames = frame_reply(_document(), chunk_size=30)
    middle = frames[:-1]
    for frame in reversed(middle):
        assert assembler.feed(frame) is None
    assert assembler.feed(frames[-1]).as_dict()["P019"] == 19


def test_assembler_ignores_acks_between_fragments():
    assembler = HanchuReplyAssembler()
    frames = frame_reply(_document(), chunk_size=30)
    for frame in frames[:-1]:
        assembler.feed(frame)
        assert assembler.feed(b"\x05\x00") is None
    assert assembler.feed(frames[-1]) is not None


def test_assembler_missing_fragment_raises_and_resets():
    assembler = HanchuReplyAssembler()
    frames = frame_reply(_document(), chunk_size=30)
    del frames[1]
    with pytest.raises(HanchuProtocolError, match="Missing"):
        for frame in frames:
            assembler.feed(frame)
    # The buffer was cleared, so a clean reply afterwards still works.
    (frame,) = frame_reply({"data": [{"k": "P071", "v": 1}]}, chunk_size=1000)
    assert assembler.feed(frame).as_dict() == {"P071": 1}
