"""The body of an email the NOC writes on a case: a small document, never raw HTML (ADR #500).

The feed's editor sends what was typed as a **document** — paragraphs, lists and a quote, made of
runs of text that may be bold, italic, underlined, a size, or a link — and this module is the
only place it becomes HTML. So no HTML a browser produced is ever relayed: the email's markup is
written here, from validated parts, with every piece of text escaped. The console renders the same
document as elements (no `innerHTML`, ADR #175), and an agent writes plain text (:func:`from_text`).

Pure: no network, no store.
"""

from __future__ import annotations

import html
import re
from typing import Any

__all__ = [
    "MAX_BLOCKS",
    "MAX_CHARS",
    "RichTextError",
    "from_text",
    "quote",
    "to_html",
    "to_text",
    "validate",
]

MAX_BLOCKS = 300
MAX_RUNS = 400
MAX_CHARS = 20000
SIZES = {"small": "12px", "large": "18px", "huge": "24px"}
_HREF = re.compile(r"^(https?://|mailto:)[^\s<>\"']{1,2000}$", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*[-*•]\s+(.*)$")


class RichTextError(ValueError):
    """A document that is not one this module writes."""


def _runs(raw: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > MAX_RUNS:
        raise RichTextError(f"{where}: a list of at most {MAX_RUNS} runs")
    out: list[dict[str, Any]] = []
    for run in raw:
        if not isinstance(run, dict) or not isinstance(run.get("t"), str):
            raise RichTextError(f"{where}: each run is {{'t': text, ...}}")
        clean: dict[str, Any] = {"t": run["t"].replace("\r", "")}
        for flag in ("b", "i", "u"):
            if run.get(flag):
                clean[flag] = True
        size = run.get("size")
        if size is not None:
            if size not in SIZES:
                raise RichTextError(f"{where}: size is one of {sorted(SIZES)}")
            clean["size"] = size
        href = run.get("href")
        if href is not None:
            if not isinstance(href, str) or not _HREF.match(href):
                raise RichTextError(f"{where}: a link is http(s):// or mailto:")
            clean["href"] = href
        if clean["t"]:
            out.append(clean)
    return out


def validate(doc: Any) -> list[dict[str, Any]]:
    """The document, checked and normalised; :class:`RichTextError` names what is wrong."""
    if not isinstance(doc, list) or not doc or len(doc) > MAX_BLOCKS:
        raise RichTextError(f"body: a list of 1 to {MAX_BLOCKS} blocks")
    out: list[dict[str, Any]] = []
    for n, block in enumerate(doc):
        if not isinstance(block, dict) or len(block) != 1:
            raise RichTextError(f"block {n}: one of p, ul, ol or quote")
        ((kind, value),) = block.items()
        if kind in ("p", "quote"):
            out.append({kind: _runs(value, f"block {n}")})
        elif kind in ("ul", "ol"):
            if not isinstance(value, list) or not value or len(value) > MAX_BLOCKS:
                raise RichTextError(f"block {n}: a list of items")
            out.append({kind: [_runs(item, f"block {n} item") for item in value]})
        else:
            raise RichTextError(f"block {n}: one of p, ul, ol or quote")
    if len(to_text(out)) > MAX_CHARS:
        raise RichTextError(f"body: at most {MAX_CHARS} characters")
    if not to_text(out).strip():
        raise RichTextError("body: write something")
    return out


def _run_html(run: dict[str, Any]) -> str:
    text = html.escape(run["t"]).replace("\n", "<br>")
    for flag, tag in (("u", "u"), ("i", "em"), ("b", "strong")):
        if run.get(flag):
            text = f"<{tag}>{text}</{tag}>"
    if "size" in run:
        text = f'<span style="font-size:{SIZES[run["size"]]}">{text}</span>'
    if "href" in run:
        text = f'<a href="{html.escape(run["href"], quote=True)}">{text}</a>'
    return text


def to_html(doc: list[dict[str, Any]]) -> str:
    """The email's HTML part: every text escaped, every tag this module's own."""
    parts: list[str] = []
    for block in doc:
        ((kind, value),) = block.items()
        if kind == "p":
            parts.append(f"<p>{''.join(_run_html(r) for r in value) or '&nbsp;'}</p>")
        elif kind == "quote":
            body = "".join(_run_html(r) for r in value)
            parts.append(
                '<blockquote style="margin:0 0 0 .8ex;border-left:2px solid #ccc;'
                f'padding-left:1ex;color:#5f5f5f">{body}</blockquote>'
            )
        else:
            items = "".join(f"<li>{''.join(_run_html(r) for r in item)}</li>" for item in value)
            parts.append(f"<{kind}>{items}</{kind}>")
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;line-height:1.45">{"".join(parts)}</body></html>'
    )


def to_text(doc: list[dict[str, Any]]) -> str:
    """The email's plain-text part, and what the feed and an agent read."""
    out: list[str] = []
    for block in doc:
        ((kind, value),) = block.items()
        if kind == "p":
            out.append("".join(r["t"] for r in value))
        elif kind == "quote":
            lines = "".join(r["t"] for r in value).split("\n")
            out.append("\n".join(f"> {line}" for line in lines))
        else:
            for n, item in enumerate(value, start=1):
                mark = f"{n}." if kind == "ol" else "-"
                out.append(f"{mark} {''.join(r['t'] for r in item)}")
    return "\n\n".join(out).strip()


def from_text(text: str) -> list[dict[str, Any]]:
    """Plain text as a document: blank lines part paragraphs, ``- item`` lines make a list."""
    doc: list[dict[str, Any]] = []
    for chunk in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = chunk.split("\n")
        if all(_LIST_ITEM.match(line) for line in lines if line.strip()):
            items = [[{"t": m.group(1)}] for line in lines if (m := _LIST_ITEM.match(line))]
            doc.append({"ul": items})
        elif chunk.strip():
            doc.append({"p": [{"t": chunk.strip()}]})
    return doc


def quote(header: str, text: str) -> dict[str, Any]:
    """What a reply quotes of the message it answers: its header line and its text."""
    body = (text or "").strip()[:4000]
    return {"quote": [{"t": f"{header}\n{body}" if body else header}]}
