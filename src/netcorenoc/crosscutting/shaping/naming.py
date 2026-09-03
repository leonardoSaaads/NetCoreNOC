"""The situation's derived name — **one implementation, two callers** (v0.16.0, DECISIONS #257).

A situation has always been `#{id}` everywhere in the console and in the API, and the id remains
the identity: it is what gets pasted into a chat during an incident, and a name is a *label on it*,
never a key. What this module adds is the label the server derives.

## What a derived name is, and what it is not

A **derived** name is a *projection* of facts already in the database: recomputable, it changes
when membership changes, and it is evidence of nothing. An **operator's** name is a *label* and
carries provenance. They live in two columns for that reason, and **no model proposes either** in
this release — a model writing *"fibre cut"* above a grouping the operator is about to judge
contaminates that judgement, which is the `incumbent_linked` mistake in a new register.

## Why membership, and only membership

The name is a function of the member count and the **distinct device addresses** of the members.
Not the root alarm: `set_root` runs on every activation and the root moves, so a name that named
the root would either go stale or cost a rewrite per trap. Membership is the coarser input and it
is the one that changes rarely, which is what lets the stored column be refreshed exactly where
`situation_alarm` changes and therefore never be stale.

Not an operator's device **label**, either: that is free text somebody typed, and folding it into a
name the *server* computed would make an operator's own words indistinguishable from a projection —
beside `operator_name`, in the same card. The address is validated at ingest.

## Why this is in `crosscutting/shaping/`

Because two layers call it and they may not call each other. `store/situations.py` writes the
stored value from the **full** membership; `crosscutting/shaping/project.py` recomputes it from the
membership a **restricted or below-editor** reader can actually see. A second implementation would
be a scope leak waiting to happen — `Storm -> 10.0.0.1` served to a reader who may not see
`10.0.0.1` discloses an address the redaction elsewhere is careful never to carry — so there is one
function and the projection is a different *input*, not a different rule.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["NO_MEMBERS", "derive_situation_name"]

#: What an empty bag is called. A real state: a situation whose members have all been moved away by
#: an operator still exists, still has a history, and still needs a heading.
NO_MEMBERS = "(no members)"

#: The longest name this function will produce. Not a validation — the inputs are validated
#: addresses and a count — but a bound, so a 500-device storm cannot put a 9 000-character string
#: in a column, an SSE frame and a card heading.
MAX_NAME_CHARS = 120


def derive_situation_name(
    addresses: Iterable[str], member_count: int, device_count: int | None = None
) -> str:
    """The situation's name, from the addresses of its members' devices and how many there are.

    Four forms, and each says something an operator acts on differently:

    ======================  =========================================
    one device, one member  ``10.0.0.1``
    one device, many        ``Storm -> 10.0.0.1``
    two devices, two        ``10.0.0.1 <-> 10.0.0.2``
    anything else           ``Storm -> 10.0.0.1 and 3 more``
    ======================  =========================================

    The two-device pair is the fibre-cut shape — two network elements, one fault between them — and
    it is the one grouping whose *shape* is the whole diagnosis. Everything wider is a storm, and
    the useful fact about a storm is where it starts, so the lowest address leads and the rest are
    counted rather than listed.

    ``addresses`` need not be sorted or unique; both are done here, so a caller cannot change the
    answer by changing the order it reads rows in. Ordering is by string, which is stable and is
    what the API and the console already sort addresses by — numeric ordering of dotted quads would
    be a second convention for no gain.

    ``member_count`` is passed separately rather than taken as ``len(addresses)`` because the two
    genuinely differ: a 500-alarm storm on one OLT has one address and five hundred members, and
    the name has to say the second thing.

    ``device_count`` is the same argument once more, and it exists so a caller may pass **fewer
    addresses than it has**. Only three are ever read — the forms turn on "one device", "exactly
    two", and "wider", and the widest names the lowest address and counts the rest — so the store
    reads `LIMIT 3` and passes the true `COUNT(DISTINCT)` beside it rather than fetching five
    hundred rows to throw all but one away. It defaults to the number of distinct addresses given,
    which is what the projection wants: there, the addresses *are* everything the reader may see.

    A ``device_count`` below the number of addresses supplied is raised to it rather than trusted.
    The two are then consistent by construction, and the alternative — a name claiming "and -2
    more" — is the kind of arithmetic that holds for every input except the one that matters.
    """
    unique = sorted({address for address in addresses if address})
    devices = max(len(unique), device_count if device_count is not None else 0)
    if member_count <= 0 or not unique:
        return NO_MEMBERS
    if devices == 1:
        name = unique[0] if member_count == 1 else f"Storm -> {unique[0]}"
    elif devices == 2 and member_count == 2:
        name = f"{unique[0]} <-> {unique[1]}"
    else:
        name = f"Storm -> {unique[0]} and {devices - 1} more"
    return name[:MAX_NAME_CHARS]
