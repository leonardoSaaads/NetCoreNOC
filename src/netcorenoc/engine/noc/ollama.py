"""What an Ollama server has, in words a custodian can choose by (ADR #477).

Ollama answers for itself: the models pulled on its machine (`GET /api/tags`), what each can do
(`POST /api/show` → `capabilities`: `completion`, `tools`, `vision`, `thinking`, `embedding`…) and
which are loaded in memory now (`GET /api/ps`). An agent the appliance runs works a case by calling
the NOC's tools, so a model **suits an agent only if it can call tools**; one that only chats can
answer but not act, and an embedding model cannot even answer. The list is ranked so the first is
the one to try: tools first, then what is already loaded, then the larger.

Everything here comes from a machine on the network and is read as data: names and words are cut
short, and nothing but the fields below is kept (`/api/show` also returns the model's licence,
template and Modelfile, which nobody needs to choose by). Pure: `llm_http.ollama_models` asks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["FIT", "billions", "rank", "row"]

#: Each verdict and what it means for an agent, as the console says it.
FIT: dict[str, str] = {
    "agents": "Calls tools: can work cases",
    "chat": "Answers but cannot call tools, so it cannot act on a case",
    "embedding": "Embeddings only: it cannot answer",
    "unknown": "This Ollama does not say what the model can do; try it on a test case first",
}
_ORDER = {"agents": 0, "unknown": 1, "chat": 2, "embedding": 3}
_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMBT])\s*$", re.IGNORECASE)
_SCALE = {"K": 1e-6, "M": 1e-3, "B": 1.0, "T": 1e3}


def billions(parameter_size: Any) -> float | None:
    """``"8.0B"`` → 8.0, ``"567M"`` → 0.567; None when Ollama did not say."""
    match = _SIZE.match(str(parameter_size or ""))
    return round(float(match[1]) * _SCALE[match[2].upper()], 3) if match else None


def _words(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(v)[:32] for v in values[:20] if isinstance(v, str)})


def _fit(capabilities: Sequence[str] | None) -> str:
    if capabilities is None:
        return "unknown"
    if "tools" in capabilities:
        return "agents"
    if "completion" in capabilities:
        return "chat"
    return "embedding" if "embedding" in capabilities else "unknown"


def _context(info: Any) -> int | None:
    """The context window from `model_info` (``<architecture>.context_length``)."""
    if not isinstance(info, Mapping):
        return None
    for key, value in info.items():
        if str(key).endswith(".context_length") and isinstance(value, int) and value > 0:
            return value
    return None


def row(tag: Mapping[str, Any], shown: Mapping[str, Any] | None, loaded: bool) -> dict[str, Any]:
    """One model as the console lists it, from its `/api/tags` row and its `/api/show` answer."""
    raw = tag.get("details")
    details: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    capabilities = _words(shown.get("capabilities")) if shown is not None else None
    if shown is not None and "capabilities" not in shown:
        capabilities = None  # an Ollama older than its capabilities field
    verdict = _fit(capabilities)
    size = tag.get("size")
    return {
        "name": str(tag.get("model") or tag.get("name") or "")[:200],
        "family": str(details.get("family") or "")[:64] or None,
        "parameter_size": str(details.get("parameter_size") or "")[:16] or None,
        "billions": billions(details.get("parameter_size")),
        "quantization": str(details.get("quantization_level") or "")[:32] or None,
        "size_bytes": size if isinstance(size, int) and size >= 0 else None,
        "context_length": _context(shown.get("model_info")) if shown is not None else None,
        "capabilities": capabilities,
        "loaded": loaded,
        "fit": verdict,
        "why": FIT[verdict],
    }


def rank(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The one to try first, first: suits agents, then loaded now, then the larger."""
    return sorted(
        (r for r in rows if r["name"]),
        key=lambda r: (_ORDER[r["fit"]], not r["loaded"], -(r["billions"] or 0.0), r["name"]),
    )
