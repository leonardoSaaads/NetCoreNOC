"""Operational logging: a root-logger redaction filter (F3) and optional JSON mode.

F3's primary fix is discipline — OptiCorr never logs a secret (the bootstrap banner is the
one sanctioned, once-only exception). This filter is defense in depth: any record that
still carries a bearer token, a cookie, or a ``key=value`` secret is masked before it can
reach a handler, so an accidental log (e.g. an exception that quotes a header) cannot leak.
"""

from __future__ import annotations

import json
import logging
import re

_REDACTED = "<redacted>"
_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(
        r"(?i)\b(password|passwd|pwd|token|secret|community|cookie|session)"
        r"(\s*[=:]\s*)(\"?)([^\s,\"'}]+)"
    ),
    re.compile(r"(?i)(opticorr_session=)([^\s;]+)"),
]


class RedactionFilter(logging.Filter):
    """Mask secret-looking substrings in the formatted message and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - never let logging raise
            return True
        redacted = self._redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True

    @staticmethod
    def _redact(text: str) -> str:
        text = _PATTERNS[0].sub(rf"\1{_REDACTED}", text)
        text = _PATTERNS[1].sub(rf"\1{_REDACTED}", text)
        text = _PATTERNS[2].sub(rf"\1\2\3{_REDACTED}", text)
        text = _PATTERNS[3].sub(rf"\1{_REDACTED}", text)
        return text


class JsonFormatter(logging.Formatter):
    """Minimal structured JSON log lines (OPTICORR_LOG_JSON=1)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(json_mode: bool) -> None:
    """Install the redaction filter on the root logger and pick plain/JSON formatting."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    if json_mode:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    redaction = RedactionFilter()
    handler.addFilter(redaction)
    root.handlers = [handler]
    root.addFilter(redaction)  # also on the logger so every handler (incl. test capture) is covered
