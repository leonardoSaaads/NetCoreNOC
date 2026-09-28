"""The shipped model: a pre-trained `gam` document and its manifest, loaded as **data**.

v0.26.0 (ADR #405). The appliance ships a model trained on generated incidents
(`eval/synth/`, `make train`) and uses it from the first trap. Two files, both JSON:

* ``linkmodel.json`` — the model document itself, the only thing that can change a decision;
* ``linkmodel.manifest.json`` — where it came from: dataset digest, seed, command, code version,
  the search, and every held-out number with its interval and sample size. The console shows it.

## Why loading is a validation, and nothing else (Part VI.3)

A customer can replace either file, and this appliance holds credentials. So the document is parsed
as JSON and put through `gam.validate` — exact keys, finite bounded numbers, strictly increasing
bins, only features this build serves, reachability — before a scorer exists. Nothing is imported,
evaluated or unpickled, and no field names code. The manifest's SHA-256 of the document is checked
too, which is **integrity, not security**: it catches a document and a manifest that do not belong
together, so the console can never describe one model while the engine runs another.

A file that fails either check is refused with a reason; the engine then runs the built-in formula
and says why, exactly as it does for any other scorer it cannot trust.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from netcorenoc.engine.model import gam

__all__ = ["ARTIFACT", "MANIFEST", "MAX_MANIFEST_BYTES", "Shipped", "load", "load_from"]

ARTIFACT = "linkmodel.json"
MANIFEST = "linkmodel.manifest.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Shipped:
    document: str
    sha256: str
    manifest: dict[str, Any]
    scorer: gam.GamScorer

    @property
    def ref(self) -> str:
        """How a decision names this model: kind and the first twelve hex digits of its hash."""
        return f"shipped:{self.sha256[:12]}"


class ShippedModelError(ValueError):
    """The shipped files are absent, malformed, or do not belong together."""


def load_from(document: str, manifest_text: str) -> Shipped:
    """Validate a document and its manifest. Raises `ShippedModelError` with the reason."""
    if len(manifest_text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ShippedModelError("the manifest is larger than any this build would write")
    try:
        manifest = json.loads(manifest_text)
    except ValueError as exc:
        raise ShippedModelError(f"the manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifact"), dict):
        raise ShippedModelError("the manifest has no artifact section")
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    if manifest["artifact"].get("sha256") != digest:
        raise ShippedModelError(
            "the model document does not match its manifest's SHA-256: they are not one artifact"
        )
    try:
        scorer = gam.load(document, scorer_id="shipped")
    except gam.GamDocumentError as exc:
        raise ShippedModelError(f"the model document was refused: {exc}") from exc
    return Shipped(document, digest, manifest, scorer)


@cache
def load() -> Shipped:
    """The packaged model. Cached: the files are part of the installed package."""
    root = resources.files("netcorenoc.engine.model.shipped")
    try:
        document = root.joinpath(ARTIFACT).read_text(encoding="utf-8")
        manifest = root.joinpath(MANIFEST).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ShippedModelError(f"the shipped model is not installed ({exc})") from exc
    return load_from(document, manifest)


def summary(shipped: Shipped) -> dict[str, Any]:
    """What the console shows about the shipped model: provenance and the headline numbers."""
    m = shipped.manifest
    return {
        "ref": shipped.ref,
        "sha256": shipped.sha256,
        "features": list(shipped.scorer.model.features),
        "grouping": shipped.scorer.model.grouping,
        "provenance": m.get("provenance", {}),
        "quality_bar": m.get("quality_bar", {}),
        "verdict": m.get("verdict", {}),
    }
