"""Four independent read models: the severity census, stats, the graph, the class catalogue.

(Plus quarantine, which is four lines of SQL and a projection.)

Each method here reads its own tables and calls nothing else in this module — which is what made
the v0.16.3 split obvious once it was looked for, and made the v0.20.0 one obvious for the second
time. The situation listing, its search filter and one situation's detail formed the first cluster
and are :mod:`netcorenoc.store.situation_reads`; the timeline's two reads and their shared scope
builder were the second and are :mod:`netcorenoc.store.timeline_models`. What is left is a plain
:class:`StoreBase` mixin of methods that share nothing but a table connection.

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
from netcorenoc.store.types import MAX_SCOPE_PARAMS

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
