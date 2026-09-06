"""Value types and module constants shared across the store package.

These sat at module level in v0.7.2's `store.py`. They live here so every mixin can import them
without reaching back into the package's ``__init__``, which would be a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import netcorenoc
from netcorenoc.ingest import known_oids

# **Resolved from the PACKAGE, not by counting `.parent`s** (v0.16.2, F102).
#
# v0.7.3 wrote `Path(__file__).parent.parent` when `store.py` became a package, noting that "the
# expression gains one `.parent` to resolve to the same directory". That is a path written as a
# NUMBER, and it is wrong the moment the module moves — which is exactly what happened to the only
# other one of these in the runtime package this release, when `routes_static.py` moved into
# `api/routes/` and silently repointed the console at a directory that does not exist.
#
# This one was still correct. It is changed anyway, because "correct until somebody moves the
# file" is the property the guard
# `tests/test_architecture.py::test_no_runtime_path_is_derived_by_counting_parents` now refuses.
MIGRATIONS_DIR = Path(netcorenoc.__file__).resolve().parent / "migrations"

TOUCH_INTERVAL_S = 5.0  # cadence for cosmetic last_seen updates on cached rows

# v0.7.1 (F38): the scoped read paths bind one parameter per in-scope NE, exactly as
# :meth:`Store.scoped_stats` has since v0.7.0. SQLite's ``SQLITE_MAX_VARIABLE_NUMBER`` is 32 766 on
# every build Python 3.12 ships with, so this cap sits comfortably below it while leaving room for
# the query's own bound values. Above the cap the scoped branch does not truncate the id list —
# that would silently answer the wrong question — it fetches unbounded and filters in Python, which
# is slower but still correct. An estate with more than this many NEs inside a single scope is far
# outside the design point of a one-file SQLite appliance.
MAX_SCOPE_PARAMS = 30_000


def class_display(label: str | None, oid: str) -> str:
    """What to call an alarm class, in one place: **declared, then derived, then the OID.**

    The same precedence `ui/app/format.js::alarmName` applies to the three fields a read model
    serves separately. It exists here because two reads — :meth:`timeline_marks` and
    :meth:`list_state_clears` — serve one composed *string* rather than the components, and they
    wrote the rule out twice as `COALESCE(cl.label, c.name, c.oid)`. One of those copies was going
    to drift (v0.16.3, DECISIONS #280).

    The middle term is a **call**, not a column. `alarm_class.name` held
    ``known_oids.trap_name(oid)`` for 48 of 48 rows on a real corpus, which makes it a stored
    derivation of the `oid` beside it, and `0008`'s rule is *derive what can be derived*. `0016`
    drops the column; this is where its value comes from now.
    """
    return label or known_oids.trap_name(oid) or oid


@dataclass(frozen=True)
class IngestResult:
    """Outcome of deduplicating one trap into the alarm table."""

    alarm_id: int
    device_id: int
    class_id: int
    activated: bool  # newly active (first ever, or re-raise after clear)
    count: int
    entity_id: int = 0  # the alarmed entity (§5.5); 0 falls back to the device at scoring


@dataclass(frozen=True)
class EdgeRow:
    """One learned pairwise statistic (affinity, precedence, or clear pair)."""

    kind: str
    a_id: int
    b_id: int
    weight: float
    n: float
    g: int


class FeedbackResult(NamedTuple):
    """Outcome of recording one feedback verdict (v0.7.1, F36).

    Two separate facts the caller must not conflate: whether the situation **exists** (a 404 if it
    does not) and whether this ``(situation, verdict)`` pair was **newly inserted**. Only a genuine
    insert may apply a learning effect — a repeat is a no-op that still answers 200, because the
    operator's statement is already on record and re-stating it carries no new information
    (DECISIONS #68).
    """

    exists: bool
    inserted: bool
    # v0.8.0: the inserted row's id, so the dataset annotation and the membership child table can
    # target it without a second SELECT. `None` on a repeat or a 404 — there is no row to annotate,
    # and defaulting it keeps every existing construction site (`FeedbackResult(exists=...,
    # inserted=...)`) working unchanged.
    id: int | None = None
