"""§10's observable-pair expression, re-derived by brute force rather than asserted in prose.

`EVIDENCE-BOUNDARY-0.9.2.md` §10 declines to store a count of *unobservable asserted pairs* on the
grounds that the surviving columns already determine it. That justification is only as good as the
arithmetic behind it, and until v0.10.0 the arithmetic was **wrong**: the document presented an
interval whose upper expression is spurious, and stated the measured case as `[2, 2]` where the
published interval evaluates to `[2, 4]`.

The claim now standing is:

    observable asserted pairs = (m - b) * ((n - m) - (h - b))

with `n = member_count`, `m = excluded_reconciled`, `h = scope_redacted_members` and
`b = excluded_reconciled_out_of_scope`. It is **exact**, not a bound: the hidden members partition
into the `b` that are marked and the `h - b` that are not, and every unmarked member is in the
remainder, so `h - b` **is** the hidden count in the remainder rather than a ceiling on it.

**Why this file exists at all.** The claim reached four documents before anyone enumerated it, and
this repository has now been bitten by an unenumerated identity twice — v0.9.2 found that
`m*r + r(r-1)/2 + m(m-1)/2 = n(n-1)/2` holds for *every* integer `m`, including `m > n`, so the test
asserting it could never have failed. Appendix B of the v0.10.0 build prompt states the rule that
follows: **before trusting an invariant, ask what value of its inputs would make it false.** So the
control below is not decoration. It runs the superseded expression through the same enumeration and
requires it to *disagree*, which is what proves the enumeration is capable of catching a wrong
formula at all.

Nothing here reads the database. It is arithmetic about a documented derivation, and it is fast
enough (~86 868 configurations) to run on every commit.
"""

from __future__ import annotations

from itertools import combinations

# `n` from 2 to 8. Two is the smallest bag with a pair in it; eight is where the enumeration stops
# being free. The count this produces — 86 868 — is quoted in §10 and in the Gate 0 evidence, and
# `test_the_enumeration_covers_what_the_document_claims` pins it so the two cannot drift apart.
MIN_N = 2
MAX_N = 8
ENUMERATED_CONFIGURATIONS = 86_868

# The case §10 cites, and the case it misstated: a six-member bag, two marks, three hidden members,
# one of the marks about a hidden member.
MEASURED_CASE = (6, 2, 3, 1)


def _published(n: int, m: int, h: int, b: int) -> int:
    """The expression now published in §10. Exact, and this file is the reason to believe that."""
    return (m - b) * ((n - m) - (h - b))


def _superseded_upper(n: int, m: int, h: int, b: int) -> int:
    """The upper half of the interval §10 used to publish. **The control.**

    It is what the count would be if the remainder held no hidden members — the special case
    `h == b` — rather than an attainable maximum given `h`. Kept here, named as superseded, because
    a test whose formula cannot be wrong proves nothing about the formula that can.
    """
    return (m - b) * (n - m)


def _configurations() -> list[tuple[int, int, int, int, int]]:
    """Every `(n, m, h, b, truth)` the enumeration covers.

    A configuration is a bag of `n` members, a **non-empty** marked set `M` (an assertion exists,
    which is what `excluded_reconciled >= 1` means), and any hidden set `H`. The truth is counted
    the long way — over the actual pairs — so it shares no arithmetic with either expression under
    test.
    """
    out: list[tuple[int, int, int, int, int]] = []
    for n in range(MIN_N, MAX_N + 1):
        members = range(n)
        subsets = [set(c) for k in range(n + 1) for c in combinations(members, k)]
        for marked in subsets:
            if not marked:
                continue
            remainder = set(members) - marked
            for hidden in subsets:
                truth = sum(
                    1 for x in marked for y in remainder if x not in hidden and y not in hidden
                )
                out.append((n, len(marked), len(hidden), len(marked & hidden), truth))
    return out


def test_the_enumeration_covers_what_the_document_claims() -> None:
    """Guard the guard. An enumeration that produced nothing would make every assertion vacuous."""
    configurations = _configurations()
    assert len(configurations) == ENUMERATED_CONFIGURATIONS, (
        f"the enumeration produced {len(configurations)} configurations, but §10 and "
        f"docs/gates/v0.10.0-phase-0.md both quote {ENUMERATED_CONFIGURATIONS}. One of them moved."
    )
    # A truth of zero everywhere would satisfy any expression that also returned zero. It does not.
    assert any(truth > 0 for *_rest, truth in configurations)
    assert any(truth == 0 for *_rest, truth in configurations)


def test_the_published_expression_is_exact_everywhere() -> None:
    """**The claim.** Not a bound — the true count, in every configuration enumerated."""
    disagreements = [
        (n, m, h, b, truth, _published(n, m, h, b))
        for n, m, h, b, truth in _configurations()
        if _published(n, m, h, b) != truth
    ]
    assert not disagreements, (
        f"§10's observable-pair expression disagreed with brute force in "
        f"{len(disagreements)} configuration(s); first five: {disagreements[:5]}"
    )


def test_the_superseded_upper_expression_disagrees() -> None:
    """**CONTROL.** The enumeration must be able to catch a wrong formula, or it proves nothing.

    This is the half of the v0.9.2 identity-test failure that was missing: an assertion no input
    can falsify is not an assertion. The superseded upper expression is a real formula that a
    careful reader published, and it is wrong on most of the space.
    """
    configurations = _configurations()
    wrong = [c for c in configurations if _superseded_upper(*c[:4]) != c[4]]
    assert wrong, (
        "the superseded upper expression agreed with brute force everywhere, which would mean this "
        "enumeration cannot distinguish a correct expression from an incorrect one"
    )
    # Measured: it is right in 21.5 % of the space, which is quoted in §10.
    share = 100.0 * (len(configurations) - len(wrong)) / len(configurations)
    assert 21.0 < share < 22.0, f"the superseded expression agreed in {share:.1f}% of the space"


def test_the_measured_case_evaluates_to_two_and_the_published_interval_did_not() -> None:
    """The specific case §10 got wrong, pinned so the correction cannot silently regress."""
    n, m, h, b = MEASURED_CASE
    assert _published(n, m, h, b) == 2, "the exact value on §10's measured case is 2"
    assert _superseded_upper(n, m, h, b) == 4, (
        "the superseded interval on this case is [2, 4] — it was published as [2, 2], which is the "
        "misstatement v0.10.0 Gate 0 corrected"
    )
    # And the brute-force truth agrees with the published expression, not with the interval's top.
    match = [c for c in _configurations() if c[:4] == MEASURED_CASE]
    assert match, "the measured case is not in the enumerated space"
    assert {c[4] for c in match} == {2}, (
        f"brute force gives {sorted({c[4] for c in match})} on the measured case, not 2"
    )
