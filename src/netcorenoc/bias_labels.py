"""What the labels ASSERT — and the aggregate primitives every bias figure is built from.

Split out of `bias.py` in v0.9.2 (DECISIONS #139), when that module reached its 400-line guard and
the evidence boundary needed a new section. The seam is by **subject**, the same one `labels.py` was
split from `capture.py` on: `bias.py` measures **what capture cost and what the corpus looks like**;
this measures **what an operator said**. The two answer different questions and only one of them
moved in this release.

**The dependency runs one way**, and it runs this way deliberately: `bias.py` imports from here, and
nothing here imports `bias`. That is why the shared primitives — `pct`, `_scalar`, `_rows`,
`distribution` — live in this module rather than in the one with the longer history. A cycle between
two halves of one report would be worse than either arrangement of the names.

Everything here obeys `bias.py`'s two rules unchanged: **aggregates only** (no NE, no address, no
OID, no varbind, no row ever leaves), and **determinism** (every query `ORDER BY`-stable, no wall
clock), because the rendered report is compared byte-for-byte in `make qa`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.store import Store

__all__ = ["assertions", "distribution", "pct"]


def pct(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100.0 * part / whole:.1f}%"


async def _scalar(store: Store, sql: str, args: tuple[Any, ...] = ()) -> Any:
    cur = await store.conn.execute(sql, args)
    row = await cur.fetchone()
    return None if row is None else row[0]


async def _rows(store: Store, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = await store.conn.execute(sql, args)
    return [dict(r) for r in await cur.fetchall()]


def _evidence_boundary(partial: list[dict[str, Any]]) -> dict[str, Any]:
    """The v0.9.2 aggregates: the disagreement, and the three scope populations.

    **The disagreement is the report's first number** — how many label rows carry a reported mark
    count differing from the one the server reconciled, and by how much. Zero on a corpus the
    shipped UI wrote, so a non-zero value means **something else has written here**. The three
    scope populations are counted separately and **never averaged** (DECISIONS #133, #126); an
    `unknown` row is uninterpretable, never assumed clean. `EVIDENCE-BOUNDARY-0.9.2.md` §6 argues
    both.
    """
    off = [r for r in partial if int(r["said"]) != int(r["m"])]
    known = [r for r in partial if r["blind"] is not None]
    checked = [r for r in known if int(r["redacted"] or 0) > 0]
    return {
        "reported_vs_reconciled_rows": len(off),
        "reported_vs_reconciled_marks": sum(int(r["said"]) - int(r["m"]) for r in off),
        "client_reported_marks": sum(int(r["said"]) for r in partial),
        "reconciled_marks": sum(int(r["m"]) for r in partial),
        "reconciled_backfilled": sum(1 for r in partial if r["source"] == "backfill"),
        "scope_populations": {
            "clean": len(known) - len(checked),
            "checked": len(checked),
            "unknown": len(partial) - len(known),
        },
        "blind_marks_in_checked": sum(int(r["blind"]) for r in checked),
    }


def distribution(values: list[int]) -> dict[str, Any]:
    """Min / median / p90 / max, and the mean to one decimal. No numpy, no statistics import —
    four indices into a sorted list, which is all a distribution of this size needs."""
    if not values:
        return {"n": 0, "min": None, "median": None, "p90": None, "max": None, "mean": None}
    ordered = sorted(values)
    n = len(ordered)
    return {
        "n": n,
        "min": ordered[0],
        "median": ordered[n // 2],
        "p90": ordered[min(n - 1, int(0.9 * n))],
        "max": ordered[-1],
        "mean": round(sum(ordered) / n, 1),
    }


async def assertions(store: Store) -> dict[str, Any]:
    """Verdicts, informativeness, the evidence boundary, and the acquisition channels.

    Caller holds ``store.lock``. Returns the keys `bias.collect` merges into its own mapping, so the
    rendered report is unchanged in shape by the split — only in the figures this release
    deliberately moves.
    """
    out: dict[str, Any] = {}
    # -- confirms versus splits ------------------------------------------------------------
    verdicts = await _rows(
        store, "SELECT verdict, COUNT(*) AS n FROM feedback GROUP BY verdict ORDER BY verdict"
    )
    out["verdicts"] = {str(r["verdict"]): int(r["n"]) for r in verdicts}
    out["labels_total"] = sum(out["verdicts"].values())

    # -- INFORMATIVENESS, not only count (v0.9.1; RECONCILED in v0.9.2) ---------------------
    # A label count became the wrong headline the moment labels started to differ in what they
    # ASSERT. Before v0.9.1 the quantity below was zero for every corpus that has ever existed:
    # nothing in the system asserted a negative pair.
    #
    # **`m` IS THE RECONCILED COUNT FROM v0.9.2, NOT THE REPORTED ONE (F46).** v0.9.1 read
    # `excluded_count`, the length of a list the CLIENT sent, so a label marking thirty ids that
    # named no member of its bag contributed +900 asserted negatives while asserting nothing.
    # `excluded_count` is neither gone nor deprecated: it is reported as `client_reported_marks`,
    # and the gap between them is now a diagnostic. Rows with no bag size, and rows with no
    # reconciled count, are excluded rather than assumed — an unknown quantity is not zero.
    partial = await _rows(
        store,
        "SELECT id, verdict, member_count AS n, excluded_reconciled AS m, excluded_count AS said, "
        "       COALESCE(excluded_truncated, 0) AS truncated, remainder_together, "
        "       excluded_reconciled_source AS source, scope_redacted_members AS redacted, "
        "       excluded_reconciled_out_of_scope AS blind "
        "FROM feedback WHERE excluded_reconciled IS NOT NULL AND member_count IS NOT NULL "
        "ORDER BY id",
    )
    negatives = [int(r["m"]) * (int(r["n"]) - int(r["m"])) for r in partial]
    out["asserted_negative_pairs"] = sum(negatives)
    out["partial_splits"] = len(partial)
    out["plain_splits"] = int(out["verdicts"].get("split", 0)) - len(partial)
    out["asserted_negatives_per_label"] = distribution(negatives)
    # Marking eight of nine is a different assertion from marking one of nine, and neither number
    # means anything without the other — so the DISTRIBUTION of |marked| relative to bag size, in
    # tenths, rather than a mean that would hide both ends.
    out["marked_fraction_tenths"] = distribution(
        [min(10, int(10 * int(r["m"]) / int(r["n"]))) for r in partial if int(r["n"]) > 0]
    )
    out["exclusions_truncated"] = sum(1 for r in partial if int(r["truncated"]))
    out.update(_evidence_boundary(partial))
    # OFFERED versus TAKEN. The shipped UI offers no control for the remainder assertion
    # (DECISIONS #127), so `offered` is 0 and so is `taken`. Printed anyway, and named, because a
    # rate over an affordance nobody was given is not a measurement of operators.
    out["remainder_offered"] = len(partial)
    out["remainder_asserted"] = sum(1 for r in partial if r["remainder_together"] is not None)
    # The pairs a partial split leaves UNASSERTED — within the remainder and within the marked set.
    # Reported beside the negatives so that v0.10.0 cannot read "not asserted positive" as
    # "asserted negative": these are the pairs about which the operator said NOTHING.
    out["unasserted_pairs_in_partial"] = sum(
        (int(r["n"]) - int(r["m"])) * (int(r["n"]) - int(r["m"]) - 1) // 2
        + int(r["m"]) * (int(r["m"]) - 1) // 2
        for r in partial
    )

    # -- per channel (v0.9.1) ----------------------------------------------------------------
    # Two populations, reported separately and NEVER averaged. A release that raised the labelling
    # rate without recording which population it raised it in would have damaged the corpus while
    # appearing to improve it (DECISIONS #126).
    out["by_channel"] = {
        str(r["c"]): {
            "labels": int(r["n"]),
            "confirm": int(r["confirms"]),
            "split": int(r["splits"]),
            "partial_splits": int(r["partials"]),
            "asserted_negatives": int(r["negatives"] or 0),
            "client_reported_marks": int(r["client_marks"] or 0),
            "restricted_scope": int(r["restricted"]),
            "client_reported": int(r["client"]),
        }
        for r in await _rows(
            store,
            # v0.9.2: `negatives` multiplies `excluded_reconciled`, not `excluded_count` (F46).
            # A SEPARATE consumer from the corpus-wide one above, with its own copy of the defect,
            # so it needs its own guard: one test covering both would leave one path unprotected.
            "SELECT COALESCE(acquisition_channel, 'unrecorded') AS c, COUNT(*) AS n, "
            "  SUM(verdict='confirm') AS confirms, SUM(verdict='split') AS splits, "
            "  SUM(excluded_reconciled IS NOT NULL) AS partials, "
            "  SUM(COALESCE(excluded_reconciled, 0) * (COALESCE(member_count, 0) "
            "      - COALESCE(excluded_reconciled, 0))) AS negatives, "
            "  SUM(COALESCE(excluded_count, 0)) AS client_marks, "
            "  SUM(COALESCE(scope_restricted, 0)) AS restricted, "
            "  SUM(client_member_digest IS NOT NULL) AS client "
            "FROM feedback GROUP BY c ORDER BY c",
        )
    }
    return out
