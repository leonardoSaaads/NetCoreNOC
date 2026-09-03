"""The bias report — **v0.8.0's deliverable**, and the release's honesty about its own dataset.

> A dataset whose bias nobody has measured is not an asset; it is a liability with a schema.

**This module trains nothing.** No fit, no weights, no split, no prediction, and no recommendation
about which model to use. It counts, it groups, and it says plainly what the numbers do *not* mean.
That restraint is the point: v0.8.0 measures so that v0.9.0 can decide with its eyes open.

**Why a CLI subcommand rather than a route.** Two reasons, and the second is stronger.

1. It adds **no HTTP surface** to the scope bypass of `FEEDBACK-DATASET-0.8-DRAFT.md` §5a. A route
   would need a capability, a scope posture, a declaration, a rate limit and a place in the
   perimeter — five things that can each be got subtly wrong — to serve a report no scoped
   principal may read anyway.
2. **A deterministic CLI report can be a guard.** Run over a fixture in `make qa` with its output
   compared byte-for-byte, it fails the suite *the day capture changes shape*. That is the
   `make eval` pattern, which is the most valuable thing this repository has, and no UI card would
   ever have that property. The report becomes a screen when the UI is rebuilt; it becomes a gate
   now, and the gate is worth more.

**Aggregates only.** Every value below is a count, a rate, a distribution or a timestamp. No NE
name, no address, no OID, no varbind value, and no row ever leaves this module.

**Determinism** is a hard requirement, exactly as it is for `eval/harness.py`: every query is
`ORDER BY`-stable, every float is formatted to a fixed precision, and no wall clock is read — ages
are computed against the newest row in the dataset, not against `time.time()`. A report that
differed between two runs could not be a gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from netcorenoc.engine.dataset.incidents import IncidentMap, resolve_all
from netcorenoc.engine.report import bias_labels

# The aggregate primitives live one module down, with the label aggregates that use them most
# (DECISIONS #139). `pct` is re-exported because `bias_report` has imported it from here since
# v0.8.0 and the split is not a reason to move a caller's import.
from netcorenoc.engine.report.bias_labels import _rows, _scalar, distribution, pct

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.store import Store

__all__ = ["collect", "pct"]

DAY_S = 86400.0


async def _incident_map(store: Store) -> IncidentMap:
    """Every labelled situation's incident, resolved through the **one** implementation.

    Deliberately not a `COALESCE` in the query above it. A merge chain can be longer than one hop —
    `merge_situations(sid, other)` marks the source merged into `sid`, and `sid` can itself later be
    merged — and a one-hop count over-states the number of independent incidents by exactly the
    amount the chain removes. On this project's own corpus the two answers agree, because every
    chain in it is one hop; **that is the hazard rather than the reassurance**, because a corpus
    with a longer chain would make this report and the estimator disagree with nothing going red.

    Reads the labelled situations rather than every situation: the count is over the *labelled*
    corpus, and resolving the whole `situation` table would be arithmetic over rows no floor
    is expressed in.
    """
    rows = await _rows(store, "SELECT DISTINCT situation_id FROM feedback ORDER BY situation_id")
    edges = await store.merge_edges()
    return resolve_all([int(r["situation_id"]) for r in rows], edges)


async def collect(store: Store) -> dict[str, Any]:
    """Every measurement §6 of the specification requires. Caller holds ``store.lock``."""
    out: dict[str, Any] = {}

    # -- the corpus, and what it cost ------------------------------------------------------
    out["pairs_sink"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'"
    )
    out["pairs_dataset"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset'"
    )
    out["observations_sink"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_observation WHERE lifecycle='sink'"
    )
    out["observations_dataset"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_observation WHERE lifecycle='dataset'"
    )
    # -- the TRAINING WINDOW, which selects rather than deletes -----------------------------
    # DECISIONS #110: `training_days` is a `WHERE` clause, not a retention policy. Nothing is
    # deleted at this boundary, so the corpus has two sizes — what it HOLDS and what a model may
    # READ — and a report that quoted only the first would overstate the usable dataset.
    #
    # Anchored on the newest promoted row rather than the wall clock, exactly as every other age in
    # this module is: a clock would make the report unreproducible and it could not be a gate.
    run = await store.latest_capture_run()
    newest_pair = await _scalar(
        store, "SELECT MAX(evaluated_at) FROM dataset_pair WHERE lifecycle='dataset'"
    )
    if run is None or newest_pair is None:
        out["pairs_in_training_window"] = out["pairs_dataset"]
        out["pairs_outside_training_window"] = 0
    else:
        cutoff = float(newest_pair) - float(run["retention_training_days"]) * DAY_S
        out["pairs_in_training_window"] = await _scalar(
            store,
            "SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset' AND evaluated_at >= ?",
            (cutoff,),
        )
        out["pairs_outside_training_window"] = int(out["pairs_dataset"] or 0) - int(
            out["pairs_in_training_window"] or 0
        )
    # Promoted pairs whose label no longer exists: features nothing can interpret. Counted, never
    # collected — a corpus with orphans is not corrupt, it is one whose USABLE size is smaller than
    # its row count, and that is exactly what this report exists to say out loud.
    out["orphaned_pairs"] = await store.orphaned_promoted_pairs()

    out["truncated_pairs"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_pair WHERE truncated=1"
    )
    out["incumbent_linked"] = await _scalar(
        store, "SELECT COUNT(*) FROM dataset_pair WHERE incumbent_linked=1"
    )
    out["storm_pairs"] = await _scalar(store, "SELECT COUNT(*) FROM dataset_pair WHERE storm=1")

    # -- what the labels ASSERT (v0.9.1; reconciled in v0.9.2) ------------------------------
    # Verdicts, informativeness, the evidence boundary and the acquisition channels, all of
    # which are aggregates over what an operator SAID rather than over what capture cost.
    # `bias_labels.py` since v0.9.2 (DECISIONS #139).
    out.update(await bias_labels.assertions(store))

    # -- the prize: judgement the product is still failing to capture (v0.9.1) ---------------
    # Every situation an operator closed without recording a verdict is a judgement that was made
    # and not captured. Gate 0 §3 measured this as UNMEASURABLE on any corpus this repository can
    # construct — the corpus labels every situation by construction, and none of its closes came
    # from an operator — so this counts it from the day the release ships, for v0.10.0's benefit.
    #
    # **v0.16.0 — the same population, spelled against the new state machine.** `closed` was a
    # `status` value covering an operator's close, the idle sweep and a self-clear alike; it is now
    # `resolved` with the reason in `resolution`, and `resolved` additionally absorbs `merged`. So
    # the predicate that preserves this population EXACTLY is `resolved` minus the merges — which
    # is what `resolution != 'merged'` says, `unattributed` included, because every historical
    # `closed` row migration `0014` rewrote landed there (DECISIONS #253).
    #
    # This report **could** now ask the sharper question these three lines have always meant —
    # *"situations an operator closed"*, `resolution = 'operator'` — and deliberately does not.
    # `tests/test_bias.py` compares this output byte for byte, the count is the one v0.10.0 was
    # given, and narrowing a measured population inside a feature release is the fix-inside-a-
    # feature this project refuses. It is a `docs/plans/v0.16.1-visualisation.md` line.
    out["situations_closed"] = await _scalar(
        store, "SELECT COUNT(*) FROM situation WHERE status='resolved' AND resolution!='merged'"
    )
    out["closed_with_verdict"] = await _scalar(
        store,
        "SELECT COUNT(*) FROM situation s WHERE s.status='resolved' AND s.resolution!='merged' "
        "AND EXISTS (SELECT 1 FROM feedback f WHERE f.situation_id = s.id)",
    )
    out["closed_without_verdict"] = int(out["situations_closed"] or 0) - int(
        out["closed_with_verdict"] or 0
    )

    # -- effective sample size -------------------------------------------------------------
    # The number that is most often reported wrongly, and the one this report exists to state
    # correctly. `n` is the number of independent BAGS, not the number of pairs.
    out["bags"] = out["labels_total"]
    out["distinct_operators"] = await _scalar(
        store, "SELECT COUNT(DISTINCT principal_ref) FROM feedback WHERE principal_ref IS NOT NULL"
    )
    out["labels_without_operator"] = await _scalar(
        store, "SELECT COUNT(*) FROM feedback WHERE principal_ref IS NULL"
    )
    # Distinct INCIDENTS, following the merge chain: two labels on situations that were merged into
    # one are one incident, and treating them as two would overstate independence.
    #
    # v0.10.1 (B1). This was `COUNT(DISTINCT COALESCE(s.merged_into, f.situation_id))` — **one
    # hop** — and v0.10.0 named it rather than fixing it, because Workstream 1 scoped
    # `store/shadow.py` and a fix inside a move is forbidden. Now it is the scope. The SQL returns
    # `situation_id` and nothing about identity; `netcorenoc.incidents` follows the edges to a fixed
    # point; and this report and the estimator and the seal cannot disagree about which incidents
    # exist, because there is one implementation and the callers cannot express the other answer.
    out["distinct_incidents"] = (await _incident_map(store)).incidents

    # -- the bag-size distribution ---------------------------------------------------------
    sizes = await _rows(
        store,
        "SELECT member_count FROM feedback WHERE member_count IS NOT NULL ORDER BY id",
    )
    out["bag_sizes"] = distribution([int(r["member_count"]) for r in sizes])
    out["empty_bags"] = await _scalar(store, "SELECT COUNT(*) FROM feedback WHERE member_count = 0")

    # -- operator concentration ------------------------------------------------------------
    by_operator = await _rows(
        store,
        "SELECT principal_ref, COUNT(*) AS n FROM feedback WHERE principal_ref IS NOT NULL "
        "GROUP BY principal_ref ORDER BY n DESC, principal_ref",
    )
    counts = [int(r["n"]) for r in by_operator]
    total_attributed = sum(counts)
    out["top_operator_share"] = pct(counts[0], total_attributed) if counts else "n/a"
    out["top3_operator_share"] = pct(sum(counts[:3]), total_attributed) if counts else "n/a"

    # -- scope ------------------------------------------------------------------------------
    out["labels_under_restricting_scope"] = await _scalar(
        store, "SELECT COUNT(*) FROM feedback WHERE scope_restricted = 1"
    )
    redacted = await _rows(
        store,
        "SELECT scope_redacted_members FROM feedback WHERE scope_restricted = 1 "
        "AND scope_redacted_members IS NOT NULL ORDER BY id",
    )
    out["redacted_members"] = distribution([int(r["scope_redacted_members"]) for r in redacted])

    # -- label latency -----------------------------------------------------------------------
    latencies = await _rows(
        store,
        "SELECT CAST(created_at - situation_opened_at AS INTEGER) AS latency FROM feedback "
        "WHERE situation_opened_at IS NOT NULL ORDER BY id",
    )
    out["latency_s"] = distribution([int(r["latency"]) for r in latencies])

    # -- coverage, three ways ----------------------------------------------------------------
    # **The denominator is the population this database has EVIDENCE of**, not the rows that
    # happen to survive in `situation` (DECISIONS #112). `prune()` collects situations on the
    # operational schedule while labels outlive them, so `COUNT(*) FROM situation` can shrink under
    # its own numerator — v0.8.0 printed a coverage rate of 300.0% on that basis. The union of
    # surviving situations and situations named by a surviving label contains the numerator by
    # construction, so the rate cannot exceed 100% for any database, and no clamp is needed.
    situations = await store.labelled_situation_population()
    labelled_situations = await _scalar(store, "SELECT COUNT(DISTINCT situation_id) FROM feedback")
    out["situations_total"] = situations
    out["situations_labelled"] = labelled_situations
    out["situation_label_rate"] = pct(int(labelled_situations or 0), int(situations or 0))
    out["pair_promotion_rate"] = pct(
        int(out["pairs_dataset"] or 0), int((out["pairs_sink"] or 0) + (out["pairs_dataset"] or 0))
    )
    per_label = await _rows(
        store,
        "SELECT coverage, COUNT(*) AS n FROM feedback WHERE coverage IS NOT NULL "
        "GROUP BY coverage ORDER BY coverage",
    )
    out["per_label_coverage"] = {str(r["coverage"]): int(r["n"]) for r in per_label}

    # -- provenance and acquisition ----------------------------------------------------------
    provenance = await _rows(
        store,
        "SELECT COALESCE(capture_provenance, 'unrecorded') AS p, COUNT(*) AS n FROM feedback "
        "GROUP BY p ORDER BY p",
    )
    out["provenance"] = {str(r["p"]): int(r["n"]) for r in provenance}
    channels = await _rows(
        store,
        "SELECT COALESCE(acquisition_channel, 'unrecorded') AS c, COUNT(*) AS n FROM feedback "
        "GROUP BY c ORDER BY c",
    )
    out["acquisition"] = {str(r["c"]): int(r["n"]) for r in channels}
    # **v0.16.0: the operator's gestures, counted per channel and never blended** (DECISIONS #126,
    # `PREREGISTRATION-0.16.0.md` §2). `acquisition` above counts LABELS, and three of this
    # release's five gestures deliberately write none — a merge because a `confirm` on the merged
    # situation would over-assert, a zombie clear and a rename because they say nothing about a
    # grouping at all. Counting only labels would therefore report that most of the release's
    # gestures never happened. These two lines are the population the census reads.
    out["gestures"] = await store.event_counts_by_channel()
    out["gestures_with_training_rows"] = await _scalar(
        store, "SELECT COUNT(*) FROM situation_event WHERE produces_training_rows = 1"
    )

    # -- server/client membership divergence -------------------------------------------------
    out["client_reported"] = await _scalar(
        store, "SELECT COUNT(*) FROM feedback WHERE client_member_digest IS NOT NULL"
    )
    out["client_diverged"] = await _scalar(
        store,
        "SELECT COUNT(*) FROM feedback WHERE client_member_digest IS NOT NULL "
        "AND member_digest IS NOT NULL AND client_member_digest <> member_digest",
    )
    out["client_truncated"] = await _scalar(
        store, "SELECT COUNT(*) FROM feedback WHERE client_truncated = 1"
    )

    # -- leakage exposure ---------------------------------------------------------------------
    # Pair epochs relative to label times: how many labelled-bag pairs were evaluated at an epoch
    # at or beyond the first label's. See §3.5a — the contamination travels through the MASSES, so
    # this is a join of epochs against label times, never a reading of the epoch alone.
    first_label = await _scalar(store, "SELECT MIN(created_at) FROM feedback")
    if first_label is None:
        out["pairs_after_first_label"] = 0
    else:
        out["pairs_after_first_label"] = await _scalar(
            store,
            "SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset' AND evaluated_at >= ?",
            (first_label,),
        )
    epochs = await _rows(
        store,
        "SELECT DISTINCT a_epoch FROM dataset_pair WHERE lifecycle='dataset' ORDER BY a_epoch",
    )
    out["distinct_a_epochs"] = len(epochs)
    # Near-duplicates from re-fire: observations sharing an `alarm_id`, which by construction are
    # the same deduplicated alarm observed at different counts.
    out["observations_distinct_alarms"] = await _scalar(
        store, "SELECT COUNT(DISTINCT alarm_id) FROM dataset_observation"
    )

    # -- retention state ----------------------------------------------------------------------
    # `run` was read above for the training window; one read, one answer.
    out["retention"] = (
        {
            "sink_days": run["retention_sink_days"],
            "sink_rows": run["retention_sink_rows"],
            "training_days": run["retention_training_days"],
            "audit_days": run["retention_audit_days"],
        }
        if run is not None
        else None
    )
    newest = await _scalar(store, "SELECT MAX(created_at) FROM feedback")
    oldest = await _scalar(store, "SELECT MIN(created_at) FROM feedback")
    out["oldest_label_age_days"] = (
        None if newest is None or oldest is None else round((newest - oldest) / DAY_S, 1)
    )
    out["capture_runs"] = await _scalar(store, "SELECT COUNT(*) FROM capture_run")
    return out
