"""What a customer's document is, and the text an agent can read from it (ADR #473).

The NOC keeps the files a customer gives it to help with troubleshooting — a topology in PDF, a
circuit list in Excel, an escalation matrix in Word, a photo of a shelf — and an agent reads them
over MCP as text. This module decides two things, from the bytes and never from what the browser
claimed:

* **what the file is** (:func:`classify`): its extension must be one of `TYPES`, and its first
  bytes must be that type's signature — a renamed executable is refused, not stored;
* **what text it holds** (:func:`extract`): plain text as it is; Excel sheets as tab-separated
  rows; Word paragraphs; slide text; PDF text from its content streams, **best effort** — a PDF
  that draws its text with embedded fonts' own codes reads as nothing, and says so. An image or a
  drawing has no text; it is kept for people to open.

Pure: bytes in, words out, every read bounded (`office.py`).
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

from netcorenoc.engine.noc import office

__all__ = ["MAX_BYTES", "TYPES", "DocError", "Kind", "classify", "extract"]

#: The largest file a customer may hand over.
MAX_BYTES = 12 * 1024 * 1024
#: The most a PDF stream may inflate to, and the most streams read.
_MAX_STREAM = 8 * 1024 * 1024
_MAX_STREAMS = 4000


class DocError(ValueError):
    """A file this appliance will not keep, with the sentence why."""


@dataclass(frozen=True)
class Kind:
    ext: str
    mime: str
    label: str
    #: The first bytes every such file starts with; None for text, which is checked by decoding.
    magic: bytes | None
    #: The icon the console draws it with (`ui/app/icons.js`).
    icon: str


_ZIP = b"PK\x03\x04"
TYPES: dict[str, Kind] = {
    k.ext: k
    for k in (
        Kind("pdf", "application/pdf", "PDF", b"%PDF-", "doc"),
        Kind(
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Excel workbook",
            _ZIP,
            "sheet",
        ),
        Kind(
            "xls",
            "application/vnd.ms-excel",
            "Excel 97-2003 workbook",
            b"\xd0\xcf\x11\xe0",
            "sheet",
        ),
        Kind(
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Word document",
            _ZIP,
            "doc",
        ),
        Kind(
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "PowerPoint presentation",
            _ZIP,
            "doc",
        ),
        Kind("vsdx", "application/vnd.ms-visio.drawing", "Visio drawing", _ZIP, "network"),
        Kind("csv", "text/csv", "CSV table", None, "sheet"),
        Kind("tsv", "text/tab-separated-values", "TSV table", None, "sheet"),
        Kind("txt", "text/plain", "Text", None, "doc"),
        Kind("md", "text/markdown", "Markdown", None, "doc"),
        Kind("png", "image/png", "PNG image", b"\x89PNG\r\n\x1a\n", "image"),
        Kind("jpg", "image/jpeg", "JPEG image", b"\xff\xd8\xff", "image"),
        Kind("jpeg", "image/jpeg", "JPEG image", b"\xff\xd8\xff", "image"),
    )
}
_NAME = re.compile(r"^[^\x00-\x1f/\\<>:\"|?*]{1,200}$")


def _text(data: bytes) -> str:
    if b"\x00" in data:
        raise DocError("a text file may not contain NUL bytes")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def classify(name: str, data: bytes) -> Kind:
    """The file's kind from its name and its first bytes, or the first thing wrong with it."""
    name = name.strip()
    if not _NAME.fullmatch(name) or name.startswith("."):
        raise DocError("name: a file name, without a path or control characters")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = TYPES.get(ext)
    if kind is None:
        raise DocError(f"type: one of {', '.join(sorted(TYPES))}")
    if not data:
        raise DocError("the file is empty")
    if len(data) > MAX_BYTES:
        raise DocError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MiB")
    if kind.magic is not None and not data.startswith(kind.magic):
        raise DocError(f"the file does not start as a {kind.label} does: it is not one")
    if kind.magic is None:
        _text(data)
    return kind


_PDF_STREAM = re.compile(rb"<<(.{0,2000}?)>>\s*stream\r?\n", re.S)
_PDF_STRING = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
_PDF_OP = re.compile(
    rb"(\[(?:[^\]\\]|\\.)*\]\s*TJ|\((?:\\.|[^\\()])*\)\s*(?:Tj|'|\")|T\*|ET)", re.S
)
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b"", b"(": b"(", b")": b")"}


def _unescape(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i : i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1 : i + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            if nxt == b"\\":
                out += b"\\"
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            i += 1
            continue
        out += ch
        i += 1
    return bytes(out)


def _inflate(data: bytes) -> bytes | None:
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(data, _MAX_STREAM)
    except zlib.error:
        return None
    return out


def pdf_text(data: bytes) -> str:
    """The text a PDF's content streams draw with plain strings, in drawing order."""
    lines: list[str] = []
    current: list[str] = []
    size = 0
    for count, match in enumerate(_PDF_STREAM.finditer(data)):
        if count >= _MAX_STREAMS or size > office.MAX_TEXT:
            break
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            break
        body = data[start:end]
        if b"FlateDecode" in match.group(1):
            inflated = _inflate(body)
            if inflated is None:
                continue
            body = inflated
        elif match.group(1).count(b"/Filter"):
            continue  # another filter: not read
        for op in _PDF_OP.finditer(body):
            token = op.group(1)
            if token in (b"T*", b"ET"):
                if current:
                    lines.append("".join(current).strip())
                    size += len(lines[-1])
                    current = []
                continue
            for piece in _PDF_STRING.findall(token):
                current.append(_unescape(piece[1:-1]).decode("latin-1"))
    if current:
        lines.append("".join(current).strip())
    text = "\n".join(line for line in lines if line and any(c.isalnum() for c in line))
    return text[: office.MAX_TEXT]


def extract(kind: Kind, data: bytes) -> tuple[str, str]:
    """The text an agent reads from the file, and a sentence on how it was read."""
    try:
        if kind.ext in ("txt", "md", "csv", "tsv"):
            return _text(data)[: office.MAX_TEXT], "read as text"
        if kind.ext == "xlsx":
            return office.xlsx_text(data), "every sheet, as tab-separated rows"
        if kind.ext == "docx":
            return office.docx_text(data), "its paragraphs"
        if kind.ext == "pptx":
            return office.pptx_text(data), "its slides' text"
        if kind.ext == "pdf":
            text = pdf_text(data)
            if text:
                return text, "its text, best effort: tables and layout are not kept"
            return "", "no text could be read: it may be scanned, or its fonts encode the text"
    except office.OfficeError as exc:
        return "", f"not read: {exc}"
    return "", f"a {kind.label} has no text to read; people open it"
