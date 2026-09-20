"""Five independent read models: stats, the graph, the class catalogue, the timeline, quarantine.

Each method here reads its own tables and calls nothing else in this module — which is what made
the v0.16.3 split obvious once it was looked for. The situation listing, its search filter and one
situation's detail formed the only cluster in the old file, and they are
:mod:`netcorenoc.store.situation_reads` now. This is a plain :class:`StoreBase` mixin again: both
sibling-inheritance edges the old header documented belonged to that cluster and went with it.

Every scoped read here is a v0.7.1 finding: F38 (``LIMIT`` bounds the *filtered* set, never the
global one) and F35 (the scope filter is on ``ne_id`` in SQL — a display string is never an
authorization key). ``ne_ids=None`` runs the unmodified v0.7.0 SQL, so parity is by construction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.base import StoreBase
from netcorenoc.store.situations import LIVE
from netcorenoc.store.types import MAX_SCOPE_PARAMS, class_display

#: The highest rank the bundled vocabulary issues, **derived from the vocabulary** rather than
#: written as 4. `known_oids.SEVERITY_VOCAB` ranks `critical 0 … indeterminate/cleared 4`, and a
#: rank above this can only have come from `severity.py::_candidate_ranks`' `int` kind, where the
#: number is a vendor's own and means nothing until it is ordered against the others (F99).
#: `app/format.js` holds the same line as `VOCAB_MAX_RANK` and `tests/test_severity.py` pins both
#: against the vocabulary itself, so the two languages cannot drift apart silently.
VOCAB_MAX_RANK = max(known_oids.SEVERITY_VOCAB.values())


def _varbinds_of(blob: Any) -> list[dict[str, Any]]:
    """The varbind list stored on an alarm row, or `[]` when it cannot be read (v0.17.1, #337).

    `alarm.varbinds` is `TEXT NOT NULL DEFAULT '[]'` and only ever written by
    `store/alarms.py` as `json.dumps` of validated models, so the unhappy path here should not
    exist. It is written anyway because this runs on a **stats route the console polls**: one
    truncated blob — a disk that filled mid-write, a row restored from a partial backup — would
    otherwise 500 the Overview for the whole estate rather than cost that one alarm its severity.
    Falling back to `[]` leaves the alarm **unplaced**, which is the honest reading of a row whose
    varbinds cannot be read, and the count it lands in is one the screen already shows.

    `store/entities.py` parses the same column with a bare `json.loads`; that is a background
    sweep where a raise is visible in the logs and costs nothing a user is waiting on.
    """
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [vb for vb in parsed if isinstance(vb, dict)]


class ReadModelsMixin(StoreBase):
    async def severity_census(self, ne_ids: frozenset[int] | None = None) -> dict[str, Any]:
        """Active alarms by band, resolved **declared first, then learned** (v0.16.7, #312).

        The one number the Overview is arranged around, and the one thing no screen could answer:
        *how many active alarms are critical, and how many has the appliance not been able to
        place at all.*

        **Why ranks and not names.** The wire carries `{rank: count}` and never a band label,
        because `app/format.js` already owns the naming and a second one here is how two surfaces
        come to disagree about the same alarm. Ranks 0-`VOCAB_MAX_RANK` came from the bundled
        vocabulary or from an operator's declaration, which the route restricts to that same
        vocabulary; anything above is a vendor's own numbering (F99) and is counted apart under
        `vendor_scaled`, because placing it needs *that NE's* whole rank set and an aggregate does
        not have one.

        **`unplaced` is a first-class count, never a zero.** `engine/correlate/severity.py` refuses
        to name a severity two independent tests have not confirmed, and on the corpus this
        repository ships that refusal covers every alarm there is. A census that reported four
        zeros beside it would be describing a quiet network.

        **The declaration is read here, not written anywhere.** v0.16.3 stores an operator's
        severity as `label(kind='severity', target_id=<alarm class>)` and never touches
        `alarm.severity`, so precedence is a read-time decision (#284, #315) — the same one
        `situation_detail` and `app/format.js::severity` make.

        **The scope is a WHERE clause** (F35/F38). Every count here enumerates something an
        out-of-scope NE could contribute to, so leaving it global would hand a scoped viewer the
        volume oracle `scoped_stats` exists to close: a rising `unplaced` with nothing visible to
        explain it says *"a storm is happening somewhere you cannot see"*. `ne_ids=None` runs the
        unmodified statement, so parity is by construction.
        """
        # The declaration joined here is per alarm CLASS, which is what an operator declares: "this
        # kind of trap is critical". One row of `label` therefore moves every active alarm of that
        # class at once, and the panel's source line says so.
        select = (
            # nosec B608 - one fixed literal from `_label_join`, chosen by a schema probe; every
            # other token here is a column name written in this string.
            "SELECT s.label AS declared, a.severity AS learned, "  # nosec B608
            "a.severity_rank AS learned_rank, a.ne_id AS ne_id, a.varbinds AS varbinds "
            "FROM alarm a " + self._label_join("s", "severity", "a.class_id")
        )
        # **v0.17.1: no GROUP BY, one row per active alarm** (DECISIONS #340). The standard read
        # resolves from the alarm's own varbinds, which are unique per row, so there is nothing left
        # to aggregate on. Measured before the change: 3.8 us to parse one varbinds blob, so a
        # 2000-alarm estate costs about 7.6 ms per census — off the trap path, on a route the
        # console polls, and paid only by the read.
        group = ""
        rows: list[Any]
        if ne_ids is None:
            cur = await self.conn.execute(f"{select}WHERE a.status='active'{group}")  # nosec B608
            rows = list(await cur.fetchall())
        elif not ne_ids:
            rows = []
        elif len(ne_ids) > MAX_SCOPE_PARAMS:
            # See MAX_SCOPE_PARAMS: an estate with more NEs in one scope than SQLite will bind is
            # filtered here rather than having its id list truncated, which would answer a
            # different question quietly. The same choice `timeline_marks` makes.
            cur = await self.conn.execute(f"{select}WHERE a.status='active'{group}")  # nosec B608
            rows = [r for r in await cur.fetchall() if r["ne_id"] in ne_ids]
        else:
            marks = ",".join("?" * len(ne_ids))
            cur = await self.conn.execute(
                f"{select}WHERE a.status='active' "  # nosec B608 - placeholders only
                f"AND a.ne_id IN ({marks}){group}",  # nosec B608 - placeholders only
                tuple(sorted(ne_ids)),
            )
            rows = list(await cur.fetchall())

        placed: dict[int, int] = {}
        # **The provenance breakdown** (v0.17.1, DECISIONS #341). Every placed alarm is counted in
        # exactly one of these, so they sum to `active - unplaced` and a reader can check that.
        # `declared` is kept at the top level too, because v0.16.7's console already reads it there
        # and this release does not get to move a key the previous one shipped.
        by_source: dict[str, int] = {"declared": 0, "standard": 0, "learned": 0}
        unplaced = vendor_scaled = declared_n = active = 0
        for row in rows:
            active += 1
            # **The precedence chain, and it is the whole of #338**: declared > standard > learned.
            # A declared severity is a vocabulary token by construction — `POST /api/labels` refuses
            # anything `known_oids.severity_rank` cannot place — so this never invents a rank.
            declared = row["declared"]
            rank: int | None
            source: str
            if declared is not None:
                rank, source = known_oids.severity_rank(declared), "declared"
            else:
                # The trap's own word, read in X.733's vocabulary (#337). Not an inference and not
                # a claim about a vendor: the device said `critical` and this believes it.
                standard = known_oids.standard_severity(_varbinds_of(row["varbinds"]))
                if standard is not None:
                    rank, source = standard[1], "standard"
                elif row["learned"] is not None:
                    rank, source = row["learned_rank"], "learned"
                else:
                    rank, source = None, "unplaced"
            if rank is None:
                # **`unplaced` stays a first-class count, never a zero** (prime directive 1). An
                # alarm whose trap carried no severity word, whose NE confirmed no severity field
                # and whose class nobody declared is counted here and rendered `—`.
                unplaced += 1
                continue
            by_source[source] += 1
            if source == "declared":
                declared_n += 1
            if rank > VOCAB_MAX_RANK:
                vendor_scaled += 1
            else:
                placed[int(rank)] = placed.get(int(rank), 0) + 1
        return {
            "active": active,
            # String keys, because this crosses JSON and an integer key would come back as one
            # anyway. The console reads them back with `Number(...)`.
            "placed": {str(rank): placed[rank] for rank in sorted(placed)},
            "unplaced": unplaced,
            "vendor_scaled": vendor_scaled,
            "declared": declared_n,
            # v0.17.1: where each placed severity came from. `vendor` is absent rather than zero —
            # #339 refuses to ship vendor rows this release, and a zero would read as "we looked and
            # found none" instead of "this arm is not built".
            "provenance": by_source,
        }

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, sql in (
            ("devices", "SELECT COUNT(*) FROM device"),
            ("classes", "SELECT COUNT(*) FROM alarm_class"),
            ("active_alarms", "SELECT COUNT(*) FROM alarm WHERE status='active'"),
            # v0.16.0 (DECISIONS #254): the LIVE population, `new` and `open` alike. The
            # correlator creates `new`, so counting `open` alone would have reported zero on a
            # working appliance the moment this release shipped — and this number has always
            # meant "situations that have not left", which is what it still means.
            ("open_situations", f"SELECT COUNT(*) FROM situation WHERE {LIVE}"),  # nosec B608
            # v0.16.4: the two halves of that population, **counted rather than derived**.
            # The console's Situations screen shows them as cards, and the live list it holds is
            # capped at 50 rows — so counting the statuses there would report a floor and call it
            # a count, which is exactly the invented number decision 2 refuses one screen over.
            # Two more `COUNT(*)` over the same small table, on a route that already runs five.
            ("new_situations", "SELECT COUNT(*) FROM situation WHERE status='new'"),
            ("working_situations", "SELECT COUNT(*) FROM situation WHERE status='open'"),
            ("quarantined", "SELECT COUNT(*) FROM quarantine"),
        ):
            cur = await self.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            out[name] = int(row[0])
        return out

    async def graph_snapshot(self, min_edge_n: float) -> dict[str, Any]:
        cur = await self.conn.execute(
            # nosec B608 - `_label_join` returns one of two fixed literals chosen by a schema
            # probe; every value in this statement is a bound parameter or a column name.
            # v0.16.4 (F105, DECISIONS #292): `d.vendor` is gone from the projection. Nothing has
            # ever written it — 25 device rows and 0 vendors after 2 252 alarms — and the two
            # screens that rendered it are gone with it. The column stays in the schema, unread.
            "SELECT d.id, d.ip, l.label, "  # nosec B608
            "(SELECT COUNT(*) FROM alarm a WHERE a.device_id=d.id AND a.status='active') "
            "AS active_alarms FROM device d "
            # v0.16.3: through the ADDRESS, which is what `device` and `ne` genuinely share — both
            # tables key on a UNIQUE `ip`. Joining `l.target_id=d.id` would work only while two
            # independent AUTOINCREMENT sequences happen to agree (DECISIONS #281).
            "LEFT JOIN ne n ON n.ip=d.ip "
            + self._label_join("l", "ne", "n.id", legacy_target="d.id")
        )
        nodes = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT a_id, b_id, weight, n FROM edge WHERE kind='device' AND n>=? AND weight>0",
            (min_edge_n,),
        )
        edges = [dict(r) for r in await cur.fetchall()]
        return {"nodes": nodes, "edges": edges}

    async def list_classes(self) -> list[dict[str, Any]]:
        """Every learned alarm class, with what an operator declared about it (v0.16.3).

        `name` and `vendor` are derived from the `oid` rather than selected beside it — `0016`
        dropped both columns as stored derivations (DECISIONS #280) — and `severity` is the
        operator's declaration for the class, the same row `situation_detail` reads.
        """
        cur = await self.conn.execute(
            # nosec B608 - two fixed literals from `_label_join`, chosen by a schema probe.
            "SELECT c.id, c.oid, l.label, s.label AS severity FROM alarm_class c "  # nosec B608
            + self._label_join("l", "class", "c.id")
            + self._label_join("s", "severity", "c.id")
            + "ORDER BY c.id"
        )
        return [
            {
                **dict(r),
                "name": known_oids.trap_name(str(r["oid"])),
                "vendor": known_oids.vendor_of(str(r["oid"])),
                "severity_rank": (
                    known_oids.severity_rank(str(r["severity"]))
                    if r["severity"] is not None
                    else None
                ),
            }
            for r in await cur.fetchall()
        ]

    async def timeline_marks(
        self,
        limit: int,
        ne_ids: frozenset[int] | None = None,
        *,
        device_ne_id: int | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        """Recent alarms as raise/clear marks over time (for the UI timeline view).

        **v0.7.1 (F35 + F38): the scope filter is on `ne_id`, in SQL.** v0.7.0 truncated
        globally and then filtered in Python by comparing the rendered ``device`` string —
        ``COALESCE(label, ip)`` — against the scope's address and label sets. That was wrong twice
        over. Labels are **not unique**, so an editor who copied an in-scope NE's label onto an
        out-of-scope one inherited its alarm timing and classes (F35); and truncating first made a
        scoped principal's marks a function of traffic they cannot see (F38). **A display string is
        never an authorization key.**

        **v0.16.1 adds two narrowing filters, and neither is a display string either.**

        * `device_ne_id` is an **NE id**, the same key the scope predicate uses — deliberately not
          the rendered `device` string the console shows. Sending that string back would make the
          v0.7.0 defect a feature request: two elements can carry one label, so a filter on it
          would silently union them. It is `AND`ed with the scope, so it can only ever *narrow*
          what the principal was already allowed to see; a scoped principal naming an NE outside
          their scope gets an empty answer through the same predicate that hides it.
        * `since` / `until` bound the window, in SQL, so `LIMIT` bounds the **filtered** set. A row
          survives if either of its marks falls in the range; the mark expansion below then emits
          only the marks that do, because a cleared alarm raised before the window is one mark
          inside it and not two.

        `ne_id` now **does** reach the mark, which is the one intentional shape change here: the
        console needs an identifier to filter by that is not a display string, and an NE id is
        already public — `/api/entities` has served it to viewers since v0.5.0.
        """
        # v0.16.3: the class name is composed by `types.class_display` after the fetch, not by a
        # `COALESCE(cl.label, c.name, c.oid)` in SQL. The middle term is now a call rather than a
        # column (`0016`), and the precedence had been written out twice — here and in
        # `list_state_clears` — which is one copy too many for a rule (DECISIONS #280).
        select = (
            # nosec B608 - two fixed literals from `_label_join`, chosen by a schema probe.
            "SELECT a.first_seen, a.cleared_at, a.ne_id, COALESCE(dl.label, d.ip) AS device, "  # nosec B608
            "cl.label AS class_label, c.oid AS class_oid FROM alarm a "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            + self._label_join("dl", "ne", "a.ne_id", legacy_target="d.id")
            + self._label_join("cl", "class", "c.id")
        )
        narrow: list[str] = []
        narrow_args: list[Any] = []
        if device_ne_id is not None:
            narrow.append("a.ne_id=?")
            narrow_args.append(device_ne_id)
        if since is not None:
            narrow.append("(a.first_seen >= ? OR a.cleared_at >= ?)")
            narrow_args.extend((since, since))
        if until is not None:
            narrow.append("(a.first_seen <= ? OR a.cleared_at <= ?)")
            narrow_args.extend((until, until))
        extra = "".join(f"AND {clause} " for clause in narrow)
        rows: list[Any]
        if ne_ids is None:
            # `WHERE 1=1` only when something narrows, so the unfiltered call stays the v0.7.0
            # statement it has been since the finding that produced it.
            head = f"{select}WHERE 1=1 {extra}" if extra else select
            cur = await self.conn.execute(
                f"{head}ORDER BY a.last_seen DESC LIMIT ?",  # nosec B608 - literal + bound values
                (*narrow_args, limit),
            )
            rows = list(await cur.fetchall())
        elif not ne_ids:
            rows = []
        elif len(ne_ids) > MAX_SCOPE_PARAMS:
            # See MAX_SCOPE_PARAMS: filter here rather than truncate the bound id list. The
            # narrowing clauses stay in the query — only the scope set is too large to bind.
            head = f"{select}WHERE 1=1 {extra}" if extra else select
            cur = await self.conn.execute(  # nosec B608 - literal + bound values
                f"{head}ORDER BY a.last_seen DESC",  # nosec B608
                tuple(narrow_args),
            )
            rows = [r for r in await cur.fetchall() if r["ne_id"] in ne_ids][:limit]
        else:
            marks_sql = ",".join("?" * len(ne_ids))
            cur = await self.conn.execute(
                f"{select}WHERE a.ne_id IN ({marks_sql}) {extra}"  # nosec B608 - placeholders only
                "ORDER BY a.last_seen DESC LIMIT ?",
                (*sorted(ne_ids), *narrow_args, limit),
            )
            rows = list(await cur.fetchall())
        marks: list[dict[str, Any]] = []
        for r in rows:
            for when, kind in ((r["first_seen"], "raise"), (r["cleared_at"], "clear")):
                if when is None:
                    continue
                if (since is not None and when < since) or (until is not None and when > until):
                    continue
                marks.append(
                    {
                        "ts": when,
                        "ne_id": r["ne_id"],
                        "device": r["device"],
                        "class": class_display(r["class_label"], str(r["class_oid"])),
                        "kind": kind,
                    }
                )
        marks.sort(key=lambda m: m["ts"])
        return marks

    async def timeline_buckets(
        self,
        *,
        bucket_s: float,
        buckets: int,
        now: float,
        ne_ids: frozenset[int] | None = None,
        device_ne_id: int | None = None,
    ) -> dict[str, Any]:
        """Raise and clear **counts per bucket** over the last ``buckets * bucket_s`` seconds.

        ## Why this exists (v0.20.0, F144)

        The Overview drew a raise/clear chart of about twenty-four columns by fetching
        ``/api/timeline?limit=1000`` and counting in the browser — **108.5 KiB on every load** to
        produce twenty-four numbers, and a thousand objects for the DOM to hold. The chart is an
        aggregate; the wire should carry the aggregate.

        It also makes a time range **mean something**. Bucketing a thousand rows client-side can
        only ever show the last thousand alarms, so a "7 days" control over that would name a
        window the data does not cover. This counts in SQL over the range asked for.

        ## It reuses the scope predicate rather than restating it

        `_timeline_scope` is the one construction of *"which alarms may this principal see"*, and
        both this and :meth:`timeline_marks` go through it. F35 and F38 were both a second copy of
        that decision drifting from the first, and a `GROUP BY` written beside it with its own
        `WHERE` would have been the third copy.

        A row contributes a raise to the bucket its `first_seen` falls in and a clear to the
        bucket of its `cleared_at`, independently — a cleared alarm raised before the window is
        one mark inside it, exactly as the mark expansion above treats it.
        """
        since = now - bucket_s * buckets
        where, args = self._timeline_scope(ne_ids, device_ne_id)
        series: dict[str, list[int]] = {
            "raises": [0] * buckets,
            "clears": [0] * buckets,
        }
        for column, key in (("a.first_seen", "raises"), ("a.cleared_at", "clears")):
            # `CAST((t - since) / bucket_s AS INTEGER)` is the bucket index, computed in SQL so
            # the rows never leave the database. `bucket_s`, `since` and the scope ids are all
            # bound values; the only interpolation is the column name, which is one of two
            # literals in this loop.
            cur = await self.conn.execute(  # nosec B608 - literal column + bound values
                f"SELECT CAST(({column} - ?) / ? AS INTEGER) AS b, COUNT(*) "  # nosec B608
                f"FROM alarm a WHERE {where} AND {column} IS NOT NULL "
                f"AND {column} >= ? AND {column} <= ? GROUP BY b",
                (since, bucket_s, *args, since, now),
            )
            for row in await cur.fetchall():
                index = int(row[0])
                if 0 <= index < buckets:
                    series[key][index] += int(row[1])
        return {
            "from": since,
            "to": now,
            "bucket_s": bucket_s,
            "buckets": buckets,
            "series": series,
        }

    def _timeline_scope(
        self, ne_ids: frozenset[int] | None, device_ne_id: int | None
    ) -> tuple[str, tuple[Any, ...]]:
        """The one place that decides which alarms a principal's timeline may count.

        Returns a `WHERE` fragment and its bound values. `device_ne_id` is `AND`ed with the scope
        so it can only narrow, never widen — the property F35 and F38 are both about.

        **A scope set too large to bind is refused, not truncated.** `timeline_marks` handles that
        case by filtering in Python after an unbounded read, which it can do because it is reading
        rows anyway; an aggregate has no rows to filter, and silently counting the whole estate
        for a scoped principal would be the leak those findings named. `_TOO_MANY_SCOPE_IDS` makes
        the answer empty instead.
        """
        clauses: list[str] = []
        args: list[Any] = []
        if ne_ids is None:
            clauses.append("1=1")
        elif not ne_ids or len(ne_ids) > MAX_SCOPE_PARAMS:
            clauses.append("0=1")
        else:
            clauses.append(f"a.ne_id IN ({','.join('?' * len(ne_ids))})")
            args.extend(sorted(ne_ids))
        if device_ne_id is not None:
            clauses.append("a.ne_id=?")
            args.append(device_ne_id)
        return " AND ".join(clauses), tuple(args)

    async def list_quarantine(self, limit: int) -> list[dict[str, Any]]:
        """Quarantine metadata only — never the raw payload (F4)."""
        cur = await self.conn.execute(
            "SELECT id, source, reason, sha256, length, first8, sanitized, raw, received_at "
            "FROM quarantine ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        out: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            row = dict(r)
            raw = row.pop("raw") or b""
            # Fallback for rows written before the F4 columns existed (v0.1.0 upgrades).
            row["sha256"] = row["sha256"] or hashlib.sha256(raw).hexdigest()
            row["length"] = row["length"] if row["length"] is not None else len(raw)
            row["first8"] = row["first8"] or raw[:8].hex()
            out.append(row)
        return out
