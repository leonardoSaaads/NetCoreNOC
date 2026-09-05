"""Operator confidence: the floor, the multiplier, and the one place both are written.

`PREREGISTRATION-0.16.0.md` §4, registered before any confidence had ever been collected:

> * confidence is recorded on every gesture, **in its own column**, per actor, on a 0-1 scale;
> * a gesture with **confidence < 0.50 produces no training row.** The action still happens — the
>   operator is running the network, not labelling it — and the event is recorded in full;
> * for confidence `c ≥ 0.50`, the training row's weight is multiplied by
>
>       m(c) = 0.6 + 0.4 · c          m(0.5) = 0.80,  m(0.8) = 0.92,  m(1.0) = 1.00
>
> * this multiplier is applied **at derivation**, composed with the existing design-effect and
>   class-balance factors, and the composition is recorded in the run's diagnostics. It is **never**
>   folded into a stored `weight`.

**Why shrunk rather than direct.** A direct weight lets a systematically overconfident operator
dominate; a pure filter throws information away. Shrinking toward 1.0 bounds the damage: a
miscalibrated operator degrades a row's contribution by **at most 20 %**, which is a known bound
rather than an unknown one.

**Why 0.6, 0.4 and a floor of 0.50.** They are conventions, chosen for a bounded multiplier over the
half-open range a self-report can meaningfully occupy, and they are *not* derived from any
measurement of operator calibration, because none exists. They are registered so that they cannot be
chosen later to suit a result — which is exactly why they live in a module of their own, hash-pinned
by the plan that names them, rather than as two literals inside a derivation somebody is editing for
another reason.

**Its own module, and small on purpose.** `training.py` is at its 400-line budget and this is not
its subject: derivation *applies* the multiplier, `engine/dataset/gestures.py` *enforces* the floor
at capture time, and neither owns the arithmetic. One home means the floor a route refuses at and
the floor a derivation drops at cannot come apart.
"""

from __future__ import annotations

__all__ = ["FLOOR", "INTERCEPT", "SLOPE", "admits", "multiplier"]

#: A gesture below this produces **no training row**. The action still happens and the event is
#: recorded in full — the operator is running the network, not labelling it.
FLOOR = 0.50

#: `m(c) = INTERCEPT + SLOPE * c`. Two named constants rather than two literals in an expression,
#: because the plan registers the pair and a guard reads them
#: (`tests/test_lifecycle.py::test_the_confidence_multiplier_is_the_one_the_plan_registered`).
INTERCEPT = 0.6
SLOPE = 0.4


def admits(confidence: float | None) -> bool:
    """Does this gesture's confidence admit a training row?

    `None` means **not reported** — the gestures that predate a confidence control, and the two the
    appliance performs itself — and it admits, because an unstated confidence is the status quo
    rather than a low one. `m(None)` is 1.0 below, so an unstated confidence shrinks nothing, which
    is exactly how every label written before this release entered training.

    A confidence of 0.0 is a *reported* value and does not admit. The difference between "not
    reported" and "reported as zero" is the whole reason the column is nullable.
    """
    return confidence is None or confidence >= FLOOR


def multiplier(confidence: float | None) -> float:
    """`m(c)` for a confidence that :func:`admits`, and **1.0 for one that was never reported**.

    Below the floor this returns `INTERCEPT + SLOPE * c` too, and that is deliberate rather than an
    oversight: this function is arithmetic and :func:`admits` is the gate, so a caller that applied
    the multiplier without the gate would produce a *small* weight instead of *no row* — a bug that
    is visible in a diagnostic rather than one that silently satisfies the plan. Appendix B's
    warning applies directly: *"a confidence multiplier that can never be below its floor"* is an
    invariant that cannot fail, and the way to avoid one is to keep the gate and the arithmetic
    separate and to test the gate.
    """
    if confidence is None:
        return 1.0
    return INTERCEPT + SLOPE * confidence
