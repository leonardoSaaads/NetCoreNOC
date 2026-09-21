"""The activity timeline: alarm marks, and the same window counted in buckets.

Split out of :mod:`netcorenoc.store.read_models` in v0.20.0, at the 400-line guard and on a seam
that module's own header already drew: it lists *"five independent read models"* and says each
*"reads its own tables and calls nothing else in this module"*. That was true of four of them.
The timeline was **three** methods sharing a private scope builder — the only cluster left after
v0.16.3 took the situation reads out — and a cluster with a shared private helper is a subject.

Two public reads over one window, and the difference between them is the whole reason both exist:

  * :meth:`timeline_marks` returns the **rows**, because the timeline view draws one mark per
    raise and per clear and a count cannot be drawn as a mark;
  * :meth:`timeline_buckets` returns the **counts**, because the Overview's activity chart draws
    twenty-four numbers and shipping it 1 000 rows to count in the browser cost 108.5 KiB a load
    and silently truncated the window to whatever those rows happened to span (v0.20.0, F144).

Both are v0.7.1 findings all the way down: F38 (``LIMIT`` bounds the *filtered* set, never the
global one) and F35 (the scope filter is on ``ne_id`` in SQL — a display string is never an
authorization key). ``ne_ids=None`` runs the unscoped SQL, so parity is by construction, and a
scope set larger than :data:`MAX_SCOPE_PARAMS` is **refused** rather than truncated.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import MAX_SCOPE_PARAMS, class_display


class TimelineMixin(StoreBase):
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
