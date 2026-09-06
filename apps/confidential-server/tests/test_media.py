from __future__ import annotations

import base64
import struct
import zlib

import pytest

import simpleunmark_confidential.media as media_module
from simpleunmark_confidential.media import (
    MEDIA_RESPONSE_MAGIC,
    MediaInput,
    clean_media,
    decode_media_request,
    encode_media_response,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _ai_tagged_png() -> bytes:
    # A valid one-pixel PNG with a generator-bearing text chunk inserted before IEND.
    tiny = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
        "AScY42YAAAAASUVORK5CYII="
    )
    iend = tiny.rfind(b"\x00\x00\x00\x00IEND")
    return tiny[:iend] + _png_chunk(b"tEXt", b"Software\x00ChatGPT") + tiny[iend:]


def test_media_frame_validates_kind_and_preserves_binary_payload() -> None:
    header = (
        b'{"assetKind":"IMAGE","extension":".png","mimeType":"image/png",'
        b'"operation":"METADATA"}'
    )
    data = _ai_tagged_png()
    frame = b"SUMM1" + struct.pack(">I", len(header)) + header + data

    decoded = decode_media_request(frame)

    assert decoded.asset_kind == "IMAGE"
    assert decoded.extension == ".png"
    assert decoded.data == data
    assert decoded.operation == "METADATA"


def test_image_cleaner_strips_generator_metadata_without_reencoding_pixels() -> None:
    original = _ai_tagged_png()

    result = clean_media(MediaInput("IMAGE", ".png", "image/png", original))

    assert result.changed is True
    assert b"ChatGPT" not in result.data
    assert b"IDAT" in result.data
    assert result.residual_warning is None


def test_media_cleaner_rejects_content_that_does_not_match_the_extension() -> None:
    with pytest.raises(ValueError, match="MEDIA_FORMAT_MISMATCH"):
        clean_media(MediaInput("IMAGE", ".jpg", "image/jpeg", _ai_tagged_png()))


def test_audio_purification_routes_to_the_destructive_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def clean_av(
        source: object,
        destination: object,
        *,
        strip_all_metadata: bool,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        source_path = media_module.Path(source)
        destination_path = media_module.Path(destination)
        destination_path.write_bytes(source_path.read_bytes())
        return {
            "format": "mp3" if calls == 1 else "mp4",
            "changed": True,
            "actions": ["drop audio metadata"],
        }

    def purify(source: object, destination: object, *, timeout: int) -> dict[str, object]:
        assert timeout == 240
        assert media_module.Path(source).read_bytes() == b"source-audio"
        media_module.Path(destination).write_bytes(b"purified-m4a")
        return {"available": True}

    monkeypatch.setattr(media_module, "_upstream_clean_av", clean_av)
    monkeypatch.setattr(media_module, "_upstream_audio_purify", purify)
    monkeypatch.setattr(media_module, "_upstream_media_has_video", lambda _: False)

    result = clean_media(
        MediaInput("AUDIO", ".mp3", "audio/mpeg", b"source-audio", "AUDIO_PURIFY")
    )

    assert result.extension == ".m4a"
    assert result.mime_type == "audio/mp4"
    assert result.data == b"purified-m4a"
    assert result.operation == "AUDIO_PURIFY"
    assert result.actions[0].startswith("destructive audio pass")


def test_response_frame_keeps_header_separate_from_file_bytes() -> None:
    data = b"binary-output"
    frame = encode_media_response({"changed": True}, data)

    assert frame.startswith(MEDIA_RESPONSE_MAGIC)
    header_size = struct.unpack(">I", frame[5:9])[0]
    assert frame[9 : 9 + header_size] == b'{"changed":true}'
    assert frame[9 + header_size :] == data
