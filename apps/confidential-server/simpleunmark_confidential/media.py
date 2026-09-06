from __future__ import annotations

import importlib
import json
import os
import struct
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

MEDIA_REQUEST_MAGIC = b"SUMM1"
MEDIA_RESPONSE_MAGIC = b"SUMR1"
MAX_MEDIA_HEADER_BYTES = 2_048

MediaAssetKind = Literal["IMAGE", "VIDEO", "AUDIO"]
MediaOperation = Literal["METADATA", "AUDIO_PURIFY"]

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".bmp", ".gif", ".tif", ".tiff"}
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".m4a"})
EXTENSIONS_BY_KIND: dict[MediaAssetKind, frozenset[str]] = {
    "IMAGE": IMAGE_EXTENSIONS,
    "VIDEO": VIDEO_EXTENSIONS,
    "AUDIO": AUDIO_EXTENSIONS,
}
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}
FORMAT_BY_EXTENSION = {
    extension: extension.removeprefix(".") for extension in MIME_BY_EXTENSION
}
FORMAT_BY_EXTENSION.update(
    {".jpg": "jpeg", ".tif": "tiff", ".mov": "mp4", ".m4v": "mp4", ".m4a": "mp4"}
)


def _load_upstream_cleaners() -> tuple[
    Callable[..., dict[str, Any]],
    Callable[..., dict[str, Any]],
    Callable[..., dict[str, Any]],
    Callable[..., bool | None],
]:
    default_scripts = (
        Path(__file__).resolve().parents[1]
        / ".vendor"
        / "watermarks-remover"
        / "service"
        / "scripts"
    )
    scripts = Path(os.environ.get("WATERMARKS_REMOVER_SCRIPTS", default_scripts))
    required = ("common.py", "image_meta.py", "av_meta.py", "clean_audio.py", "clean_video.py")
    if any(not (scripts / filename).is_file() for filename in required):
        raise RuntimeError(
            "watermarks-remover media source is unavailable; run pnpm server:setup"
        )
    sys.path.insert(0, str(scripts))
    try:
        image_module = importlib.import_module("image_meta")
        av_module = importlib.import_module("av_meta")
        audio_module = importlib.import_module("clean_audio")
    finally:
        sys.path.remove(str(scripts))
    return (
        cast("Callable[..., dict[str, Any]]", image_module.clean_image),
        cast("Callable[..., dict[str, Any]]", av_module.clean_av),
        cast("Callable[..., dict[str, Any]]", audio_module.audio_purify),
        cast("Callable[..., bool | None]", audio_module.media_has_video),
    )


(
    _upstream_clean_image,
    _upstream_clean_av,
    _upstream_audio_purify,
    _upstream_media_has_video,
) = _load_upstream_cleaners()


@dataclass(frozen=True, slots=True)
class MediaInput:
    asset_kind: MediaAssetKind
    extension: str
    mime_type: str
    data: bytes
    operation: MediaOperation = "METADATA"


@dataclass(frozen=True, slots=True)
class MediaResult:
    asset_kind: MediaAssetKind
    extension: str
    mime_type: str
    data: bytes
    changed: bool
    actions: tuple[str, ...]
    residual_warning: str | None
    operation: MediaOperation = "METADATA"


def decode_media_request(payload: bytes) -> MediaInput:
    if len(payload) < len(MEDIA_REQUEST_MAGIC) + 4 or not payload.startswith(MEDIA_REQUEST_MAGIC):
        raise ValueError("INVALID_MEDIA_FRAME")
    header_size = struct.unpack(">I", payload[5:9])[0]
    if header_size == 0 or header_size > MAX_MEDIA_HEADER_BYTES or 9 + header_size > len(payload):
        raise ValueError("INVALID_MEDIA_FRAME")
    try:
        header = json.loads(payload[9 : 9 + header_size].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("INVALID_MEDIA_FRAME") from error
    if not isinstance(header, dict) or set(header) != {
        "assetKind",
        "extension",
        "mimeType",
        "operation",
    }:
        raise ValueError("INVALID_MEDIA_FRAME")
    asset_kind = header.get("assetKind")
    extension = header.get("extension")
    mime_type = header.get("mimeType")
    operation = header.get("operation")
    if asset_kind not in EXTENSIONS_BY_KIND:
        raise ValueError("UNSUPPORTED_MEDIA_KIND")
    if not isinstance(extension, str):
        raise ValueError("UNSUPPORTED_MEDIA_FORMAT")
    extension = extension.lower()
    if extension not in EXTENSIONS_BY_KIND[asset_kind]:
        raise ValueError("UNSUPPORTED_MEDIA_FORMAT")
    if (
        not isinstance(mime_type, str)
        or not 1 <= len(mime_type) <= 100
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in mime_type)
    ):
        raise ValueError("INVALID_MEDIA_TYPE")
    if operation not in {"METADATA", "AUDIO_PURIFY"}:
        raise ValueError("INVALID_MEDIA_OPERATION")
    if operation == "AUDIO_PURIFY" and asset_kind != "AUDIO":
        raise ValueError("INVALID_MEDIA_OPERATION")
    data = payload[9 + header_size :]
    if not data:
        raise ValueError("EMPTY_MEDIA")
    return MediaInput(
        asset_kind=cast("MediaAssetKind", asset_kind),
        extension=extension,
        mime_type=mime_type,
        data=data,
        operation=cast("MediaOperation", operation),
    )


def encode_media_response(header: dict[str, object], data: bytes) -> bytes:
    encoded_header = json.dumps(
        header,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    if len(encoded_header) > 32_000:
        raise ValueError("MEDIA_RESULT_TOO_LARGE")
    return MEDIA_RESPONSE_MAGIC + struct.pack(">I", len(encoded_header)) + encoded_header + data


def clean_media(media: MediaInput) -> MediaResult:
    with tempfile.TemporaryDirectory(prefix="simpleunmark-media-") as temp_directory:
        workdir = Path(temp_directory)
        source = workdir / f"input{media.extension}"
        metadata_destination = workdir / f"metadata-cleaned{media.extension}"
        source.write_bytes(media.data)
        if media.asset_kind == "IMAGE":
            report = _upstream_clean_image(
                source,
                metadata_destination,
                strip_all_metadata=True,
                remove_pixel=None,
            )
        else:
            report = _upstream_clean_av(
                source,
                metadata_destination,
                strip_all_metadata=True,
            )
        reports = [report]
        detected_format = str(report.get("format") or "unknown")
        if detected_format != FORMAT_BY_EXTENSION[media.extension]:
            raise ValueError("MEDIA_FORMAT_MISMATCH")

        destination = metadata_destination
        output_extension = media.extension
        output_mime_type = MIME_BY_EXTENSION[media.extension]
        purification: dict[str, Any] | None = None
        if media.operation == "AUDIO_PURIFY":
            has_video = _upstream_media_has_video(metadata_destination)
            if has_video is not False:
                raise ValueError("AUDIO_STREAM_REQUIRED")
            purified = workdir / "audio-purified.m4a"
            purification = _upstream_audio_purify(
                metadata_destination,
                purified,
                timeout=240,
            )
            if not purification.get("available"):
                raise RuntimeError("AUDIO_PURIFICATION_FAILED")
            destination = workdir / "cleaned.m4a"
            final_report = _upstream_clean_av(purified, destination, strip_all_metadata=True)
            if str(final_report.get("format") or "unknown") != "mp4":
                raise RuntimeError("AUDIO_PURIFICATION_FAILED")
            reports.append(final_report)
            output_extension = ".m4a"
            output_mime_type = MIME_BY_EXTENSION[output_extension]

        output = destination.read_bytes()
        raw_actions = [
            str(action)
            for current_report in reports
            for action in list(current_report.get("actions") or [])
        ]
        changed = media.operation == "AUDIO_PURIFY" or any(
            bool(current_report.get("changed")) for current_report in reports
        )
        final_report = reports[-1]
        residual = bool(
            final_report.get("still_has_c2pa") or final_report.get("still_has_ai_metadata")
        )
        actions = tuple(
            action.replace(str(workdir), "work")[:300] for action in raw_actions[:30]
        )

    if purification is not None:
        actions = (
            "destructive audio pass: 1.08x tempo, +2 semitones, EQ, 96 kbps AAC",
            *actions[:29],
        )
    return MediaResult(
        asset_kind=media.asset_kind,
        extension=output_extension,
        mime_type=output_mime_type,
        data=output,
        changed=changed,
        actions=actions,
        residual_warning=(
            "Some provenance metadata could not be safely removed from this file."
            if residual
            else None
        ),
        operation=media.operation,
    )
