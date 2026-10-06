"""The restructuring gestures: move, merge, split, and the answer to a proposal (v0.29.0).

Split out of `models.py` mechanically, at the 400-line module guard, exactly as
`models_maintenance.py` and `models_decider.py` were: `models.py` re-exports every name here **by
identity**, so *"what can a caller send this appliance"* is still one import list in one file
(ADR #374). These four are the gestures that change a grouping, each a label the slow loop reads.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

__all__ = ["MergeIn", "MoveIn", "ProposalIn", "SplitIn"]


class MoveIn(BaseModel):
    """Move alarms out of this situation and into another. **The release's product.**

    The only gesture that yields a negative and a positive from one action at pair granularity:
    the moved alarms against the members they leave are asserted negative, and against the members
    they join positive. Both situations are named, so both are scope-checked.

    v0.29.0: `alarm_ids` moves several alarms as **one** gesture; `alarm_id` (one alarm) is kept
    for existing clients. Exactly one of the two is given. Moving them one request at a time
    asserted each against the others it travelled with — a negative the operator never said.
    """

    alarm_id: int | None = Field(default=None, ge=1)
    alarm_ids: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=1, max_length=4096
    )
    to_situation_id: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _one_form(self) -> MoveIn:
        if (self.alarm_id is None) == (self.alarm_ids is None):
            raise ValueError("name the alarms to move as `alarm_id` or as `alarm_ids`, not both")
        return self

    def moving(self) -> list[int]:
        """The alarms to move, de-duplicated, in the order given."""
        if self.alarm_id is not None:
            return [self.alarm_id]
        return list(dict.fromkeys(self.alarm_ids or ()))


class MergeIn(BaseModel):
    """Merge another situation into this one. Every cross pair is asserted positive."""

    from_situation_id: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ProposalIn(BaseModel):
    """An operator's answer to a pending proposal (v0.27.0, ADR #428). ``accept`` merges it into
    the situation it proposed to join; ``reject`` makes it a situation of its own."""

    decision: Literal["accept", "reject"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SplitIn(BaseModel):
    """Split the named members out of this situation into a new one.

    Every cross pair between the departing members and the remainder is asserted negative, **and
    nothing else** — the pairs within each half stay unknown, which is DECISIONS #124's reading of
    a marked split and is what the label this writes records.

    `max_length` is a parse bound rather than a validation of meaning, the same reasoning
    `FeedbackIn.excluded_ids` carries: it exists to stop an unbounded parse, and the semantic bound
    is `MAX_CLIENT_MEMBERS` inside `Exclusion.accept`, which truncates and records that it did.
    """

    alarm_ids: list[int] = Field(min_length=1, max_length=4096)
    confidence: float = Field(ge=0.0, le=1.0)
