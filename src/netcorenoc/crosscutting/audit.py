"""Tamper-evident audit log: canonicalization, hash chain, verify and export.

Every audit row is hash-chained: ``entry_hash = sha256(prev_hash + canonical)`` where
``canonical`` is the row's fields serialized deterministically. The genesis ``prev_hash``
is 64 zeros. The ``audit_log`` table is append-only at the storage layer (SQLite
``BEFORE UPDATE``/``BEFORE DELETE`` triggers, migration ``0002``), so any edit to history
breaks the chain and is detectable by :func:`verify_chain`.

The event catalog (frozen in ``docs/gates/v0.2-phase-1.md``) is enumerated in
:data:`ACTIONS`. Writers live at the API call sites (Phase 3 S4); this module owns the
cryptographic contract and the offline tooling.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from netcorenoc.store import Store

GENESIS_HASH = "0" * 64

# Fields that make up the canonical form, in the exact key set the hash commits to.
CANONICAL_KEYS = (
    "id",
    "ts",
    "actor",
    "role",
    "source_ip",
    "action",
    "object_type",
    "object_id",
    "outcome",
    "details",
)

# Frozen event catalog. Every mutating endpoint and sensitive read maps to one of these.
# Retired in v0.3.0 (§5.8): "legacy_token.used" — the shared token was removed, so no new
# rows are written, but historical rows remain valid and still verify against the hash chain.
ACTIONS: frozenset[str] = frozenset(
    {
        # auth
        "login.ok",
        "login.fail",
        "login.lockout",
        "logout",
        "password.change",
        # management
        "user.create",
        "user.update",
        "user.delete",
        "role.change",
        "token.create",
        "token.revoke",
        "config.change",
        # operation
        "feedback",
        "label.set",
        "situation.close",
        # v0.16.0 — the five operator gestures. Five actions rather than one `situation.restructure`
        # for the reason there are five routes (DECISIONS #255): an auditor asking *"who moved an
        # alarm out of this incident, and when"* must not have to read a details blob to find out
        # which kind of restructuring a row records. `alarm.clear` is named for its object, because
        # a zombie clear is a fact about an ALARM and not about a grouping.
        "situation.move",
        "situation.merge",
        "situation.split",
        "situation.name",
        "alarm.clear",
        "prune.manual",
        # sensitive reads
        "quarantine.read",
        "audit.read",
        "audit.export",
        # v0.3.0 — the learned entity model
        "entity.promote",  # system actor: an NE subdivided into a learned entity
        "entity.reset",  # admin: reset an NE's learned entity key
        "profile.reset",  # admin: reset an NE's varbind profiler state
        "severity.confirm",  # system actor: a varbind confirmed as an NE's severity field
        "ingest.gap",  # system actor: a durable record of dropped traps
        # v0.6.0 — the scoring seam. Retuning the link formula is a system-wide logic change,
        # so it is admin-only and every apply, rollback, preview, and degradation is recorded.
        "scorer.config.update",  # admin: appended a config and/or moved the active pointer
        "scorer.preview",  # admin: ran a read-only what-if over recent alarms
        "scorer.fallback",  # system actor: the active scorer failed; engine fell back to defaults
        # v0.7.0 — governance. A change to who may *do* what, or see *which* NEs, is a change to
        # the perimeter itself: admin-only, before/after captured, and one row per change.
        "rbac.policy.update",  # admin: set, rolled back, or cleared the capability policy
        "scope.policy.update",  # admin: set, rolled back, or cleared the scoping policy
        # v0.8.0 — the feedback dataset's retention, and THE FIRST DESTRUCTIVE CONTROL IN THE
        # PRODUCT. Every other admin configuration here is reversible: `scorer_config` is
        # append-only and rollback is a pointer move, governance policies are versioned. LOWERING
        # RETENTION DELETES ROWS AND THERE IS NO ROLLBACK FOR A DELETE — an admin moving the
        # training tier from twelve months to three destroys nine months of human labels, the most
        # expensive and least reconstructible asset in the system. Both halves are recorded: the
        # preview the operator saw, and the reduction they then applied.
        "retention.preview",  # admin: read-only count of what a reduction would destroy
        "retention.change",  # admin: applied a retention policy (before/after captured)
        # system actor: a stored policy could not be parsed and the fail-safe engaged (the ceiling
        # for capabilities, deny-for-viewer/editor for scope). Recorded once per policy version, so
        # a silent degradation is impossible (DECISIONS #55).
        "governance.fallback",
        # v0.11.0 — champion/challenger. EXACTLY the actions this release makes possible and no
        # others (DECISIONS #162): promotion applied, promotion refused, and — because this release
        # makes the seal SPENDABLE for the first time — seal construction and seal spend.
        #
        # `0012` declined to put seal access in this chain, on the grounds that "nothing here is
        # reachable from a route, no principal performs it". **v0.11.0 removes that premise**: an
        # admin approving a promotion is a principal, on a route, whose approval may cause the seal
        # to be spent. The decision is revisited because its precondition changed, not overridden.
        # `holdout_access` remains the primary record and stays append-only; this is the
        # operator-facing half.
        #
        # The v0.9.2 reconciliation-drift gap (`SECURITY-REVIEW-0.10.0.md` §3.4) is NOT closed here.
        # Nothing in this release makes it newly reachable, and a release that closed unrelated gaps
        # because it happened to have the file open would be sizing its work by convenience.
        "promotion.applied",  # admin: a model version became the active scorer
        "promotion.refused",  # admin: a promotion was proposed and the evidence did not support it
        "seal.construct",  # system actor: the sealed holdout was cut, once, ever
        "seal.spend",  # the sealed membership was read; the query count moved
    }
)

OUTCOMES: frozenset[str] = frozenset({"ok", "denied", "error"})

# Defense-in-depth: details must be redacted at the call site, but if any of these keys
# ever reach the writer they are dropped rather than persisted.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "password",
        "new_password",
        "old_password",
        "session",
        "session_id",
        "token",
        "token_value",
        "secret",
        "community",
        "authorization",
        "cookie",
    }
)


def redact_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Drop any forbidden key (a password, session id, token value, community string)."""
    if not details:
        return {}
    return {k: v for k, v in details.items() if k.lower() not in _FORBIDDEN_DETAIL_KEYS}


def canonical(entry: dict[str, Any]) -> str:
    """Deterministic serialization the entry hash commits to."""
    payload = {k: entry.get(k) for k in CANONICAL_KEYS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_hash(prev_hash: str, canonical_str: str) -> str:
    return hashlib.sha256((prev_hash + canonical_str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_broken_id: int | None
    reason: str | None
    final_hash: str

    def render(self) -> str:
        if self.ok:
            return f"audit chain OK: {self.checked} entries verified; final hash {self.final_hash}"
        return (
            f"audit chain BROKEN at id {self.first_broken_id}: {self.reason} "
            f"(after verifying {self.checked} entries)"
        )


def _row_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the canonical field dict from a stored row (details as parsed JSON)."""
    return {
        "id": int(row["id"]),
        "ts": row["ts"],
        "actor": row["actor"],
        "role": row["role"],
        "source_ip": row["source_ip"],
        "action": row["action"],
        "object_type": row["object_type"],
        "object_id": row["object_id"],
        "outcome": row["outcome"],
        "details": json.loads(row["details"]),
    }


async def write_event(
    store: Store,
    *,
    ts: float,
    actor: str,
    role: str | None,
    source_ip: str | None,
    action: str,
    outcome: str,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Append one chained audit row. Caller holds ``store.lock`` (HTTP-side, serialized).

    Returns the new row's ``entry_hash``. The row id is reserved as ``MAX(id)+1`` under
    the lock so the id inside the canonical form matches the stored id; only the oldest
    rows are ever pruned, so this never collides with a live row.
    """
    assert action in ACTIONS, f"unknown audit action {action!r}"
    assert outcome in OUTCOMES, f"unknown audit outcome {outcome!r}"
    prev_hash = await store.audit_last_hash()
    next_id = await store.audit_next_id()
    entry = {
        "id": next_id,
        "ts": ts,
        "actor": actor,
        "role": role,
        "source_ip": source_ip,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "outcome": outcome,
        "details": redact_details(details),
    }
    entry_hash = compute_hash(prev_hash, canonical(entry))
    await store.audit_insert(entry, prev_hash, entry_hash)
    return entry_hash


async def verify_chain(store: Store) -> VerifyResult:
    """Walk the chain in id order; report the first row whose hash or linkage is wrong."""
    rows = await store.audit_all()
    checked = 0
    prev_hash = GENESIS_HASH
    final_hash = GENESIS_HASH
    for row in rows:
        entry = _row_entry(row)
        stored_prev = row["prev_hash"]
        stored_entry = row["entry_hash"]
        rid = int(row["id"])
        # Linkage: each row's prev_hash must equal the previous row's entry_hash. The first
        # surviving row chains to genesis unless the log was pruned (then id != 1 and its
        # prev_hash is the archived boundary hash, which we accept as the new anchor).
        if checked == 0:
            if rid == 1 and stored_prev != GENESIS_HASH:
                return VerifyResult(False, checked, rid, "genesis prev_hash not zero", final_hash)
            prev_hash = stored_prev
        elif stored_prev != prev_hash:
            return VerifyResult(
                False, checked, rid, "prev_hash does not match previous entry_hash", final_hash
            )
        recomputed = compute_hash(stored_prev, canonical(entry))
        if recomputed != stored_entry:
            return VerifyResult(
                False, checked, rid, "entry_hash mismatch (row tampered)", final_hash
            )
        prev_hash = stored_entry
        final_hash = stored_entry
        checked += 1
    return VerifyResult(True, checked, None, None, final_hash)


async def export_ndjson(store: Store) -> tuple[list[str], str]:
    """Return (NDJSON lines, final chain hash). Each line is one full audit row."""
    rows = await store.audit_all()
    lines = [json.dumps(dict(row), sort_keys=True, separators=(",", ":")) for row in rows]
    final_hash = rows[-1]["entry_hash"] if rows else GENESIS_HASH
    return lines, final_hash
