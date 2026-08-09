"""The request surface: every pydantic model the API accepts, in one file.

Deliberately **not** fragmented per route group (SCOPE-0.7.2 §3.4). The whole point of a request
model is that it is the boundary between untrusted bytes and the handler, and that boundary is
easier to audit as one list than as eleven fragments across nine modules: a reviewer asking "what
can a caller send this appliance, and what are the bounds?" gets one file.

Pydantic bounds the *shape*. Where a field has semantics beyond its shape — the scorer parameters,
the governance documents — the single semantic authority is named in the model's own docstring and
runs in addition, so the precise reason for a rejection always comes from the one validator.

`QuietServer` lives here rather than in `app.py` because it is a shipped type `main.py` imports,
not part of building the application.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any, Literal

import uvicorn
from pydantic import BaseModel, Field

from netcorenoc import auth, scoring

MAX_LABEL_CHARS = 120
MAX_NOTE_CHARS = 500


class FeedbackIn(BaseModel):
    verdict: Literal["confirm", "split"]
    # v0.8.0 §5.4b — the client's report of what it rendered. **Optional and additive**: an old
    # client, a `curl` call, or a UI that does not send it still works, and absence means
    # *unrecorded*, never a guess. This is the only change to the feedback contract.
    #
    # `max_length` here is a *parse* bound, not a validation of meaning: a longer list is truncated
    # by `ClientFingerprint.accept` and the truncation is recorded on the row. Pydantic rejecting an
    # over-long list would make the bound a refusal, and **rejection is the wrong primitive for an
    # observation** (§2.1, one level down) — so the ceiling is set well above `MAX_CLIENT_MEMBERS`
    # and exists only to stop an unbounded parse.
    #
    # The ids are **never validated against anything**. An alarm id the principal cannot see, or
    # that does not exist, is recorded exactly as reported and changes nothing about the response,
    # its status or its timing. That is what keeps this from becoming an existence oracle for a
    # scoped editor, and it is a security requirement rather than a nicety.
    member_ids: list[int] | None = Field(default=None, max_length=4096)
    updated_at: float | None = None
    # v0.9.1 — **which members do not belong.** The whole point of the release, and it is optional
    # and additive for exactly the reasons `member_ids` is: a `curl` call, an old client, or a UI
    # that does not send it all keep working, and **absence means the operator marked nothing** —
    # a plain `split` — never a guess.
    #
    # It asserts `marked-by-rest` negative and **nothing else**; the pairs within the remainder and
    # within the marked set stay unknown (DECISIONS #124). Meaningful only on a `split`: a
    # `confirm` already asserts every pair positive, so an exclusion on one is a contradiction and
    # is dropped rather than recorded (`LabelContext.for_verdict`).
    #
    # Same three properties as `member_ids`, on the same write path and for the same reasons:
    # **bounded, never rejected, never used to validate the existence of anything.** The parse
    # ceiling is a parse bound, not a validation of meaning — `Exclusion.accept` truncates at
    # `MAX_CLIENT_MEMBERS` and records that it did, because rejection is the wrong primitive for
    # an observation and a silence would make the bound invisible in the data.
    excluded_ids: list[int] | None = Field(default=None, max_length=4096)
    # The operator's SEPARATE assertion about the rest. `None` means **not asserted**, and it is
    # never inferred from `excluded_ids` — excluding two of nine says nothing whatever about the
    # other seven. The shipped UI does not offer it (DECISIONS #127); the contract carries it for
    # the rebuild.
    remainder_together: bool | None = None


class CloseIn(BaseModel):
    """v0.9.1 — closing a situation, optionally carrying the verdict the operator already formed.

    **Every field is optional and the whole body may be absent.** `POST .../close` with no body, or
    with `{}`, behaves exactly as it did at v0.9.0: the situation closes, no label is written, and
    nothing about the response changes. That is the point — Workstream 2 raises the labelling rate
    by merging two gestures into one, and it must never make the first gesture harder.

    A verdict recorded here is written with **`acquisition_channel = 'close'`**, never `'organic'`
    (DECISIONS #126): closing selects for *resolved* incidents, which is a different population
    from the one an operator browses and labels spontaneously, and two populations blended into one
    column destroy the bias characterisation retroactively.

    The label fields mirror `FeedbackIn` exactly, because a label acquired here must be the same
    label in every respect but its channel — same bag, same fingerprint discipline, same scope
    fingerprint, same bounds.
    """

    verdict: Literal["confirm", "split"] | None = None
    member_ids: list[int] | None = Field(default=None, max_length=4096)
    updated_at: float | None = None
    excluded_ids: list[int] | None = Field(default=None, max_length=4096)
    remainder_together: bool | None = None


class LabelIn(BaseModel):
    kind: Literal["device", "class"]
    id: int
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)


class RetentionIn(BaseModel):
    """The three dataset retention tiers (v0.8.0 §7).

    The bounds here are sanity rails, not the policy: the ORDERING invariant
    (`sink < training <= audit`) is validated by `capture.RetentionPolicy.validate`, which returns a
    precise reason rather than a bare rejection — an admin faced with "invalid" on a form of four
    numbers cannot act on it.

    `preview` defaults to True and that is the safety property, not a convenience: **a reduction is
    previewed before it is applied.** The caller must pass `preview=False` deliberately, having seen
    the count, which is the v0.6.0 pattern for exactly this class of decision.
    """

    sink_days: float = Field(gt=0, le=3650)
    sink_rows: int = Field(gt=0, le=1_000_000_000)
    training_days: float = Field(gt=0, le=3650)
    audit_days: float = Field(gt=0, le=3650)
    preview: bool = True


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    new_password: str | None = Field(default=None, max_length=auth.MAX_PASSWORD)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    new_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    role: Literal["viewer", "editor", "admin"]


class RoleIn(BaseModel):
    role: Literal["viewer", "editor", "admin"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["viewer", "editor", "admin"]


class ConfigIn(BaseModel):
    allowlist: str = Field(max_length=1024)
    retention_days: float = Field(gt=0, le=3650)


class ScorerParamsIn(BaseModel):
    """A candidate parameter set. Pydantic bounds the *shape*; `scoring.validate_params` is the
    single semantic authority (it also rejects the degenerate combinations a range check misses),
    so both run and the precise reason always comes from the one validator."""

    w_t: float = Field(ge=0.0, le=1.0)
    w_a: float = Field(ge=0.0, le=1.0)
    w_e: float = Field(ge=0.0, le=1.0)
    tau_s: float = Field(gt=0.0, le=scoring.MAX_TAU_S)
    threshold: float = Field(ge=0.0, le=1.0)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


class ScorerRollbackIn(BaseModel):
    """A candidate parameter set. Pydantic bounds the *shape* only."""

    config_id: int = Field(ge=1)


class PolicyIn(BaseModel):
    """One governance write: apply a new document, roll back to a version, or clear the policy.

    Exactly one of the three is meaningful per call, checked in the handler so the error names the
    actual problem. `clear` is the recovery path back to the shipped baseline — the compiled
    ceiling and full visibility — and it removes only the pointer: the history rows are immutable
    and survive, so what was once active stays answerable.
    """

    document: dict[str, Any] | None = None
    policy_id: int | None = Field(default=None, ge=1)
    clear: bool = False
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


class QuietServer(uvicorn.Server):
    """Uvicorn server that leaves signal handling to the NetCoreNOC process."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield
