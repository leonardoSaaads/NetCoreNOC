"""The store's SQL dialect on PostgreSQL: SQLite's placeholders, translated once (ADR #452).

The store's statements keep SQLite's ``?`` and ``:name`` placeholders; :func:`translate` rewrites
them to ``$n`` once per distinct statement, skipping quoted text and comments, and decides whether
the statement returns rows and whether it writes — which is what lets `db.Connection` open a
transaction on the first write, as Python's ``sqlite3`` did. PostgreSQL's ``?`` jsonb operators
are therefore unusable in store SQL; ``jsonb_exists()`` and ``@>`` say the same thing.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["arguments", "rowcount", "translate"]

_ROWS = re.compile(r"^\s*(SELECT|WITH|VALUES|SHOW|EXPLAIN|TABLE)\b|\bRETURNING\b", re.IGNORECASE)
_WRITE_HEAD = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|SET|LOCK|GRANT|REVOKE|COMMENT)\b",
    re.IGNORECASE,
)
_WRITE_ANYWHERE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|FOR\s+(NO\s+KEY\s+)?UPDATE|FOR\s+SHARE|"
    r"pg_advisory_xact_lock|prune_audit_log)\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=4096)
def translate(sql: str) -> tuple[str, tuple[str, ...] | None, bool, bool]:
    """``(postgres_sql, names, returns_rows, writes)`` for one store statement.

    ``names`` is the order of ``:name`` parameters, or ``None`` for positional ``?`` ones.
    Quoted literals, quoted identifiers and ``--`` comments are copied untouched, and ``::`` (a
    cast) is never read as a parameter.
    """
    out: list[str] = []
    names: list[str] = []
    positional = 0
    i, length = 0, len(sql)
    while i < length:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < length:
                if sql[j] == c:
                    if j + 1 < length and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = length if j < 0 else j
            out.append(sql[i:j])
            i = j
        elif c == "?":
            positional += 1
            out.append(f"${positional}")
            i += 1
        elif (
            c == ":"
            and i + 1 < length
            and (sql[i + 1].isalpha() or sql[i + 1] == "_")
            and (i == 0 or sql[i - 1] != ":")
        ):
            j = i + 1
            while j < length and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            name = sql[i + 1 : j]
            if name not in names:
                names.append(name)
            out.append(f"${names.index(name) + 1}")
            i = j
        else:
            out.append(c)
            i += 1
    text = "".join(out)
    if positional and names:
        raise ValueError("a statement mixes ? and :name parameters")
    returns_rows = bool(_ROWS.search(text))
    writes = bool(_WRITE_HEAD.match(text) or _WRITE_ANYWHERE.search(text))
    return text, (tuple(names) if names else None), returns_rows, writes


def arguments(names: tuple[str, ...] | None, params: Any) -> Sequence[Any]:
    if names is None:
        return tuple(params) if params else ()
    assert isinstance(params, Mapping), "a :name statement takes a mapping"
    return tuple(params[name] for name in names)


def rowcount(status: str) -> int:
    """`UPDATE 3`, `DELETE 0`, `INSERT 0 1` -> the number of rows; -1 when the status has none."""
    tail = status.rsplit(" ", 1)[-1] if status else ""
    return int(tail) if tail.isdigit() else -1
