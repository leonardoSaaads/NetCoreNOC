"""Reading the Office files a customer hands the NOC: a sheet's rows, a document's text (ADR #473).

A customer's circuit list arrives as an Excel workbook and its topology as a Word or PowerPoint
file; the appliance reads them with the standard library alone — each is a ZIP of XML parts — so no
parser is added for files that come from outside. That is also why the reading is defensive:

* **A ZIP bomb is refused**: at most ``MAX_MEMBERS`` parts, none inflating past ``MAX_PART``, and
  every read bounded, whatever the archive's own header claims.
* **An XML part with a DTD is refused** before it is parsed — no entity is ever expanded, so neither
  "billion laughs" nor an external entity can reach the parser. The parts Office writes have none.
* What is read is bounded too: ``MAX_ROWS`` rows, ``MAX_CELLS`` cells, ``MAX_TEXT`` characters.

Pure, and nothing here touches the network or the disk.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET  # nosec B405 - DTDs are refused before parsing (`_xml`)
import zipfile
from collections.abc import Iterator

__all__ = [
    "MAX_TEXT",
    "OfficeError",
    "docx_text",
    "pptx_text",
    "xlsx_rows",
    "xlsx_text",
]

MAX_MEMBERS = 2000
MAX_PART = 32 * 1024 * 1024
MAX_ROWS = 5000
MAX_CELLS = 400_000
MAX_TEXT = 1_000_000

_NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_COLUMN = re.compile(r"^([A-Z]+)")


class OfficeError(ValueError):
    """A file that is not the Office file it says it is, or one this appliance will not open."""


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise OfficeError("not an Office file (it is not a ZIP archive)") from exc
    if len(archive.infolist()) > MAX_MEMBERS:
        raise OfficeError(f"more than {MAX_MEMBERS} parts")
    return archive


def _part(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART:
        raise OfficeError(f"{name}: larger than {MAX_PART // (1024 * 1024)} MiB once inflated")
    with archive.open(info) as handle:
        data = handle.read(MAX_PART + 1)
    if len(data) > MAX_PART:
        raise OfficeError(f"{name}: larger than {MAX_PART // (1024 * 1024)} MiB once inflated")
    return data


def _xml(data: bytes) -> ET.Element:
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise OfficeError("an XML part declares a DTD; it is not opened")
    try:
        return ET.fromstring(data)  # nosec B314 - no DTD, so no entity expands
    except ET.ParseError as exc:
        raise OfficeError("an XML part is not well formed") from exc


def _column(ref: str) -> int:
    match = _COLUMN.match(ref)
    if not match:
        return 0
    n = 0
    for ch in match.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared(archive: zipfile.ZipFile) -> list[str]:
    data = _part(archive, "xl/sharedStrings.xml")
    if data is None:
        return []
    root = _xml(data)
    return ["".join(t.text or "" for t in si.iter(f"{{{_NS['s']}}}t")) for si in root]


def _sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Each sheet's name and its part, in the workbook's order."""
    book = _part(archive, "xl/workbook.xml")
    rels = _part(archive, "xl/_rels/workbook.xml.rels")
    if book is None:
        raise OfficeError("not a spreadsheet (no workbook part)")
    targets: dict[str, str] = {}
    if rels is not None:
        for rel in _xml(rels):
            target = rel.get("Target", "")
            target = target.lstrip("/")
            targets[rel.get("Id", "")] = target if target.startswith("xl/") else f"xl/{target}"
    out = []
    for sheet in _xml(book).iter(f"{{{_NS['s']}}}sheet"):
        part = targets.get(sheet.get(_REL, ""), "")
        if part:
            out.append((sheet.get("name", "Sheet"), part))
    return out


def _rows(archive: zipfile.ZipFile, part: str, shared: list[str]) -> Iterator[list[str]]:
    data = _part(archive, part)
    if data is None:
        return
    cells = 0
    s = _NS["s"]
    for count, row in enumerate(_xml(data).iter(f"{{{s}}}row")):
        if count >= MAX_ROWS:
            return
        values: dict[int, str] = {}
        for cell in row.iter(f"{{{s}}}c"):
            cells += 1
            if cells > MAX_CELLS:
                raise OfficeError(f"more than {MAX_CELLS} cells")
            kind = cell.get("t", "")
            text = ""
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{{{s}}}t"))
            else:
                v = cell.find(f"{{{s}}}v")
                raw = (v.text or "") if v is not None else ""
                if kind == "s" and raw.isdigit() and int(raw) < len(shared):
                    text = shared[int(raw)]
                else:
                    text = raw
            values[_column(cell.get("r", ""))] = text
        width = max(values) + 1 if values else 0
        yield [values.get(i, "") for i in range(width)]


def xlsx_rows(data: bytes, sheet: int = 0) -> list[list[str]]:
    """One sheet's rows as text cells (the first sheet unless told), merged cells as Excel stores
    them: the value in the first cell, the rest empty."""
    archive = _zip(data)
    sheets = _sheets(archive)
    if not sheets:
        raise OfficeError("the workbook has no sheet")
    if not 0 <= sheet < len(sheets):
        raise OfficeError(f"sheet: from 0 to {len(sheets) - 1}")
    return list(_rows(archive, sheets[sheet][1], _shared(archive)))


def xlsx_text(data: bytes) -> str:
    """Every sheet as tab-separated text, headed by its name — what an agent reads."""
    archive = _zip(data)
    shared = _shared(archive)
    out: list[str] = []
    size = 0
    for name, part in _sheets(archive):
        out.append(f"# {name}")
        for row in _rows(archive, part, shared):
            line = "\t".join(row).rstrip()
            if line:
                out.append(line)
                size += len(line)
            if size > MAX_TEXT:
                return "\n".join(out)[:MAX_TEXT]
    return "\n".join(out)


def docx_text(data: bytes) -> str:
    """A Word document's paragraphs, one per line."""
    part = _part(_zip(data), "word/document.xml")
    if part is None:
        raise OfficeError("not a Word document (no document part)")
    w = _NS["w"]
    lines = [
        "".join(t.text or "" for t in p.iter(f"{{{w}}}t")) for p in _xml(part).iter(f"{{{w}}}p")
    ]
    return "\n".join(line for line in lines if line.strip())[:MAX_TEXT]


def pptx_text(data: bytes) -> str:
    """A presentation's slides' text, slide by slide."""
    archive = _zip(data)
    slides = sorted(
        (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]),
    )
    out: list[str] = []
    for number, name in enumerate(slides, start=1):
        part = _part(archive, name)
        if part is None:
            continue
        texts = [t.text or "" for t in _xml(part).iter(f"{{{_NS['a']}}}t")]
        out.append(f"# Slide {number}\n" + "\n".join(t for t in texts if t.strip()))
    return "\n\n".join(out)[:MAX_TEXT]
