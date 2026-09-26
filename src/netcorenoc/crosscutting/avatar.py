"""Profile photos: what the appliance accepts, decided from the bytes alone (v0.25.0, ADR #402).

The console re-encodes a chosen photo on a canvas before sending it — a centred square, at most
320 px, WebP or PNG - which strips EXIF, colour profiles and anything appended to the file. This
module does not trust that: it re-checks what arrived, with no image library and no decoder.

* **The format comes from the magic number**, never from a header the client sent or a file name:
  PNG, WebP or JPEG, and nothing else. SVG is refused by construction — it is a document that can
  carry script, not an image.
* **The dimensions come from the format's own header** (PNG `IHDR`, WebP `VP8 `/`VP8L`/`VP8X`,
  JPEG `SOFn`): at least 16 px, at most 320 px per side. A file whose header cannot be read is
  refused rather than guessed at.
* **At most 64 KiB.** A 192 px WebP is 5-12 KiB; the ceiling is several times that and still small
  enough that 300 photos are a few megabytes in the database and nothing in the server's memory.

The bytes are then served back only as the type decided here, with `nosniff`, a closed CSP and a
digest-versioned URL (`api/routes/people.py`), so a file crafted to be something else as well is
never interpreted as anything but an image.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

MAX_BYTES = 64 * 1024
MAX_SIDE = 320
MIN_SIDE = 16

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: JPEG start-of-frame markers carry the dimensions; C4 (DHT), C8 (JPG) and CC (DAC) do not.
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


class AvatarError(ValueError):
    """A photo the appliance will not store. The message is operator-facing and names the rule."""


@dataclass(frozen=True)
class Avatar:
    mime: str
    width: int
    height: int
    image: bytes
    sha256: str


def _png(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise AvatarError("the PNG has no header")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _webp(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        raise AvatarError("the WebP is truncated")
    if struct.unpack("<I", data[4:8])[0] + 8 != len(data):
        raise AvatarError("the WebP's length does not match its header")
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise AvatarError("the WebP frame has no start code")
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        if data[20] != 0x2F:
            raise AvatarError("the lossless WebP has no signature")
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    raise AvatarError("the WebP carries no image frame")


def _jpeg(data: bytes) -> tuple[int, int]:
    at = 2
    while at + 9 < len(data):
        if data[at] != 0xFF:
            raise AvatarError("the JPEG's segments are malformed")
        marker = data[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = struct.unpack(">H", data[at + 2 : at + 4])[0]
        if length < 2:
            raise AvatarError("the JPEG's segments are malformed")
        if marker in _SOF:
            height, width = struct.unpack(">HH", data[at + 5 : at + 9])
            return int(width), int(height)
        at += 2 + length
    raise AvatarError("the JPEG has no frame header")


def validate(data: bytes) -> Avatar:
    """The photo, checked — or `AvatarError` naming the first rule it breaks."""
    if not data:
        raise AvatarError("no image was sent")
    if len(data) > MAX_BYTES:
        raise AvatarError(f"the image is larger than {MAX_BYTES // 1024} KiB")
    if data.startswith(PNG_MAGIC):
        mime, (width, height) = "image/png", _png(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime, (width, height) = "image/webp", _webp(data)
    elif data[:3] == b"\xff\xd8\xff":
        mime, (width, height) = "image/jpeg", _jpeg(data)
    else:
        raise AvatarError("only PNG, WebP or JPEG images are accepted")
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise AvatarError(
            f"the image is {width}x{height}; each side must be {MIN_SIDE}-{MAX_SIDE} px"
        )
    return Avatar(mime, width, height, data, hashlib.sha256(data).hexdigest())
