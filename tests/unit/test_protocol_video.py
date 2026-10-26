import struct

import pytest

from phonectl.core.protocol.video import (
    ProtocolError,
    SessionPacket,
    is_session_header,
    parse_codec_id,
    parse_device_name,
    parse_media_header,
    parse_session_header,
)


def test_device_name_is_nul_padded_and_trimmed():
    raw = b"Pixel 7".ljust(64, b"\x00")
    assert parse_device_name(raw) == "Pixel 7"


def test_device_name_wrong_length():
    with pytest.raises(ProtocolError):
        parse_device_name(b"short")


def test_codec_ids():
    assert parse_codec_id(b"h264") == "h264"
    assert parse_codec_id(b"h265") == "h265"
    with pytest.raises(ProtocolError):
        parse_codec_id(b"xxxx")


def test_session_header_parses_size():
    # flags = 0x80000000 (session flag), width=1080, height=2400
    hdr = struct.pack(">III", 0x8000_0000, 1080, 2400)
    assert is_session_header(hdr)
    assert parse_session_header(hdr) == SessionPacket(width=1080, height=2400, client_resized=False)


def test_session_header_client_resized_flag():
    hdr = struct.pack(">III", 0x8000_0001, 720, 1280)
    assert parse_session_header(hdr).client_resized is True


def test_media_header_key_frame_with_pts():
    pts = 123_456
    hdr = struct.pack(">QI", (1 << 61) | pts, 999)
    assert not is_session_header(hdr)
    m = parse_media_header(hdr)
    assert (m.pts, m.key_frame, m.config, m.size) == (pts, True, False, 999)


def test_media_header_config_packet_has_no_pts():
    hdr = struct.pack(">QI", 1 << 62, 25)
    m = parse_media_header(hdr)
    assert (m.config, m.pts, m.key_frame, m.size) == (True, None, False, 25)


def test_media_header_rejects_session_header():
    with pytest.raises(ProtocolError):
        parse_media_header(struct.pack(">III", 0x8000_0000, 1, 1))
