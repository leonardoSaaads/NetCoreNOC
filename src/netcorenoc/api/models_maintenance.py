"""The maintenance-window request surface — **designed for an agent as much as for a human**.

`models.py`'s docstring says the request surface is deliberately one file, because the boundary
between untrusted bytes and a handler is easier to audit as one list than as fragments. That
reasoning is unchanged and this module is not an exception to it: `models.py` re-exports every
name here **by identity**, so a reviewer asking *"what can a caller send this appliance"* still
reads one import list and one file's worth of names. The split is mechanical — the 400-line module
guard, which `models.py` was already within eleven lines of — and `tests/test_maintenance_api.py`
asserts the re-export is by identity rather than by copy, the same guard `crosscutting/rbac/`
carries for the same reason (ADR #374).

## Why the validation here is stricter than usual

Phase 2 is an AI agent that calls this API. An agent cannot read a docstring and cannot guess; it
corrects its own request from the error it gets back. So every refusal here **names the field and
the rule** rather than reporting that a body was invalid, and every enum is a `Literal` so
`/openapi.json` carries the permitted values rather than the word `string`. An MCP tool generated
from a vague schema is a vague tool.

## RFC 3339 with an explicit offset, always

A naive datetime is **rejected**, not assumed to be UTC and not assumed to be the appliance's local
zone. *"10:00"* means nothing without a place, and an appliance that guessed would schedule an
operator in Brasília for work in Riyadh at the wrong hour — which is the exact failure D2 exists to
prevent. `AwareInstant` is the one type that enforces it and every instant on this surface uses it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from netcorenoc.crosscutting import shaping
from netcorenoc.ingest import known_oids

#: Bounds. Each is a *parse* bound on an untrusted body, not a statement about meaning.
MAX_WINDOW_NAME = 120
MAX_WINDOW_DESCRIPTION = 2000
MAX_TARGETS = 500
MAX_RULES = 2000
MAX_OID_CHARS = 200
MAX_IDEMPOTENCY_KEY = 200

#: The highest rank a severity rule may name. Derived from the vocabulary rather than written as
#: 4, so a band added to `SEVERITY_VOCAB` moves the line with it — F92's lesson, one resource over.
MAX_SEVERITY_RANK = max(known_oids.SEVERITY_VOCAB.values())

#: A window may not be declared longer than this. Not a policy about maintenance — a bound on an
#: untrusted body, so a typo of `2026` where `2h` was meant cannot silence an estate for a year.
MAX_WINDOW_S = 30 * 86400.0

#: Nor shorter than this. A window of zero length is a request the operator did not mean to make.
MIN_WINDOW_S = 60.0


def _aware(value: Any) -> float:
    """An RFC 3339 instant **with an explicit offset**, as seconds since the epoch.

    Refuses a naive datetime by name, because the alternative is worse in a specific way: an
    appliance that assumed UTC would accept *"10:00"* from an operator in Brasília and schedule the
    window for 07:00 their time, silently, and the work would happen outside it.

    Accepts what `datetime.fromisoformat` accepts, which since Python 3.11 is the whole of RFC
    3339 including a trailing `Z`. A float is accepted too and is taken as an epoch instant, which
    is what an agent that has already resolved a zone will send.
    """
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"{value!r} is not an RFC 3339 instant. Send one with an explicit offset, for "
                "example '2026-05-14T10:00:00-03:00' or '2026-05-14T13:00:00Z'."
            ) from exc
    else:
        raise ValueError("expected an RFC 3339 instant string")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            f"{value!r} has no UTC offset. A naive datetime is refused rather than assumed to be "
            "UTC: '10:00' means nothing without a place, and guessing would schedule work in the "
            "wrong hour for any site outside this appliance's own zone. Send "
            "'2026-05-14T10:00:00-03:00' or '2026-05-14T13:00:00Z'."
        )
    return moment.timestamp()


#: The instant type every field on this surface uses. One annotation rather than a validator per
#: field, so a field added later cannot quietly accept a naive datetime.
AwareInstant = Annotated[float, Field(json_schema_extra={"format": "date-time"})]


def _valid_oid(value: str) -> str:
    """Dots and digits, non-empty arcs, and nothing else.

    Not a general OID parser: this is the subtree an operator typed, it goes into a comparison and
    never into a query, and the narrow shape is what keeps it that way. A refusal names the rule so
    an agent can correct itself.
    """
    text = value.strip()
    parts = text.split(".")
    if not text or not all(part.isdigit() for part in parts):
        raise ValueError(
            f"{value!r} is not an OID. Give dot-separated digits, for example "
            "'1.3.6.1.4.1.2011.5.25.31.1.1.1.1'."
        )
    return text


class CollectionRuleIn(BaseModel):
    """One D4 rule: what still gets through on one target.

    **The shape is validated against the kind**, so a rule that names a `kind` and then carries
    another kind's fields is refused rather than stored with the extra ones ignored. An agent that
    sent `{"kind": "oid", "severity_rank": 0}` meant something, and silently dropping half of it
    would give it a window that does not do what it asked for.
    """

    ne_id: int
    kind: Literal["severity", "oid", "slot"]

    #: `kind="severity"`: an X.733 rank, **smaller is more severe**. 0 admits only `critical`.
    severity_rank: int | None = Field(default=None, ge=0, le=MAX_SEVERITY_RANK)

    #: `kind="oid"`: the subtree, matched on **arc boundaries** (`engine/mw/rules.py`).
    oid_root: str | None = Field(default=None, max_length=MAX_OID_CHARS)
    #: Where to look for it. Both are supported and the rule says which — see `MatchOn`.
    match_on: Literal["trap", "varbind"] | None = None

    #: `kind="slot"`: a window inside the window.
    slot_starts_at: AwareInstant | None = None
    slot_ends_at: AwareInstant | None = None

    _coerce = field_validator("slot_starts_at", "slot_ends_at", mode="before")(
        lambda v: None if v is None else _aware(v)
    )

    @field_validator("oid_root")
    @classmethod
    def _check_oid(cls, value: str | None) -> str | None:
        return None if value is None else _valid_oid(value)

    @model_validator(mode="after")
    def _check_kind(self) -> CollectionRuleIn:
        required: dict[str, tuple[str, ...]] = {
            "severity": ("severity_rank",),
            "oid": ("oid_root",),
            "slot": ("slot_starts_at", "slot_ends_at"),
        }
        every = {"severity_rank", "oid_root", "match_on", "slot_starts_at", "slot_ends_at"}
        mine = set(required[self.kind]) | ({"match_on"} if self.kind == "oid" else set())
        for field in required[self.kind]:
            if getattr(self, field) is None:
                raise ValueError(f"a rule of kind {self.kind!r} needs {field!r}")
        extra = sorted(f for f in every - mine if getattr(self, f) is not None)
        if extra:
            raise ValueError(
                f"a rule of kind {self.kind!r} must not carry {extra} — those belong to another "
                "kind, and storing them would give you a rule that does not do what you asked"
            )
        if self.kind == "slot":
            assert self.slot_starts_at is not None and self.slot_ends_at is not None
            if self.slot_ends_at <= self.slot_starts_at:
                raise ValueError("slot_ends_at must be after slot_starts_at")
        return self

    def as_row(self) -> dict[str, Any]:
        """The shape `Store.set_window_rules` writes. Validated already; this only renames."""
        return {
            "ne_id": self.ne_id,
            "kind": self.kind,
            "severity_rank": self.severity_rank,
            "oid_root": self.oid_root,
            # The default is the narrower reading of the maintainer's own example: his subtree is
            # shaped like a table column carried in varbinds rather than like a notification OID.
            "match_on": (self.match_on or "varbind") if self.kind == "oid" else None,
            "slot_starts_at": self.slot_starts_at,
            "slot_ends_at": self.slot_ends_at,
        }


class _WindowBody(BaseModel):
    """The fields a create and an update share. Not a route model on its own."""

    name: str = Field(min_length=1, max_length=MAX_WINDOW_NAME)
    description: str = Field(default="", max_length=MAX_WINDOW_DESCRIPTION)
    organization_id: int | None = None
    #: A canonical IANA zone. **The city is a label the console resolves before it gets here**:
    #: three of the cities an operator names — Washington, Brasília, Beijing — have no zone of
    #: their own, so storing the label would store something `ZoneInfo` cannot load (D2, ADR #362).
    tz: str = Field(default=shaping.DEFAULT_ZONE, max_length=64)
    starts_at: AwareInstant
    ends_at: AwareInstant
    all_day: bool = False
    patch_s: float = Field(default=600.0, ge=0.0, le=86400.0)
    ledger_enabled: bool = True
    visibility: Literal["editors", "everyone"] = "editors"
    targets: list[int] = Field(default_factory=list, max_length=MAX_TARGETS)
    rules: list[CollectionRuleIn] = Field(default_factory=list, max_length=MAX_RULES)

    _instants = field_validator("starts_at", "ends_at", mode="before")(_aware)

    @field_validator("tz")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        """**This host must be able to load it**, which is the only question worth asking.

        A zone the appliance cannot resolve is one whose DST rules it does not have, so a window in
        it would be scheduled against an offset nobody can compute. Refusing at the boundary is
        better than storing it and being wrong twice a year.
        """
        if not shaping.resolves(value):
            raise ValueError(
                f"{value!r} is not a time zone this appliance can resolve. Use a canonical IANA "
                "zone such as 'America/Sao_Paulo' — GET /api/timezones lists what is available, "
                "and searching it by city is what the console does. Note that 'America/Brasilia', "
                "'America/Washington' and 'Asia/Beijing' are not IANA zones: those cities are "
                "'America/Sao_Paulo', 'America/New_York' and 'Asia/Shanghai'."
            )
        return value

    @model_validator(mode="after")
    def _check_window(self) -> _WindowBody:
        span = self.ends_at - self.starts_at
        if span < MIN_WINDOW_S:
            raise ValueError(
                f"ends_at must be at least {MIN_WINDOW_S:.0f}s after starts_at (got {span:.0f}s)"
            )
        if span > MAX_WINDOW_S:
            raise ValueError(
                f"a window may not be longer than {MAX_WINDOW_S / 86400:.0f} days "
                f"(got {span / 86400:.1f}); this is a bound on the request, not a policy about "
                "maintenance — declare a shorter window and extend it"
            )
        named = {rule.ne_id for rule in self.rules}
        unknown = sorted(named - set(self.targets))
        if unknown:
            raise ValueError(
                f"rules name element(s) {unknown} that are not targets of this window. A rule on "
                "an untargeted element can never fire."
            )
        return self


class MaintenanceWindowIn(_WindowBody):
    """`POST /api/maintenance-windows`.

    `idempotency_key` is the caller's own opaque string. **An agent that retries after a timeout
    must not create two windows** (Part III), and a retry carrying the same key returns the window
    the first call made — the same id and the same body — rather than a 409 the agent has to
    interpret.
    """

    idempotency_key: str | None = Field(default=None, max_length=MAX_IDEMPOTENCY_KEY)


class MaintenanceWindowUpdateIn(_WindowBody):
    """`POST /api/maintenance-windows/{wid}`. The whole window, as the operator is looking at it.

    A full body rather than a patch, for the reason `set_window_targets` replaces rather than
    merges: *"remove this host"* has no expression in a patch of a list, and a rule left behind on
    an element nobody targets any more is a rule that can never fire.
    """


class MaintenanceWindowPreviewIn(_WindowBody):
    """`POST /api/maintenance-windows/preview` — **the dry run, and the same code the form uses.**

    *"If I create this, which devices are covered, how many currently active alarms would be
    affected, and does it need confirmation?"* The most useful call an agent can make before
    committing, and the console's live preview issues exactly this — so the number an operator
    reads and the number an agent reads cannot disagree.
    """


class WindowExtendIn(BaseModel):
    """`POST /api/maintenance-windows/{wid}/extend` — *"the work is running long."*

    An instant rather than a duration, because *"another hour"* is ambiguous about what it is an
    hour from — the declared end, or now — and an engineer at a tower with patchy signal should not
    have to know which the appliance meant.
    """

    ends_at: AwareInstant

    _instants = field_validator("ends_at", mode="before")(_aware)


class OrganizationIn(BaseModel):
    """`POST /api/organizations`. **Attribution, not isolation** — see `store/organizations.py`."""

    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")


class NeOrganizationIn(BaseModel):
    """`POST /api/entities/{ne_id}/organization`. Which provider owns this element.

    One field, and it is an **id rather than a slug**: the slug is the operator's own handle and a
    rename must not silently re-point every element that named it. The route refuses an id no
    organization holds and says which route lists the ones that do, because an agent corrects its
    request from the error.
    """

    organization_id: int = Field(ge=1)
