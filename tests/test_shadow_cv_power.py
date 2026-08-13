"""The detection threshold, pinned against an independent Monte-Carlo (v0.10.1, A2).

**The next disagreement should be detected, not argued.** v0.10.0 measured a disagreement between
the shipped closed form and the ratified plan's §3.1 table, reasoned about it in a gate document, an
ADR and a security review, and reached the wrong conclusion — because the "independent" Monte-Carlo
it checked the form against **shared the form's assumption**. Two methods that share an assumption
are one method (Appendix B).

So this file does not argue. It runs a power simulation that shares no arithmetic with
`minimum_detectable_difference` and asserts the two land in the same place:

* the closed form solves an equation;
* the simulation **draws two binomials, runs a two-sided pooled z-test, and counts rejections**,
  searching a fixed absolute grid for the smallest true difference that reaches 80 % power.

The only thing they have in common is `Z_ALPHA`, which is the *definition* of the alpha both are
stated at rather than an assumption either makes — a simulation that rejected at a different
critical value would be measuring a different test.

**Determinism.** Its own generator (xorshift64\\*, deliberately not the LCG in `shadow_cv`), a fixed
seed, an exact inverse-CDF binomial sampler, and a fixed grid. Two runs and two processes produce
identical numbers, which is this project's oldest property.
"""

from __future__ import annotations

import bisect
import math
from itertools import pairwise

from netcorenoc.shadow_cv import Z_ALPHA, minimum_detectable_difference

# The registered base rate. §3.1's table is evaluated here and nowhere else varied.
P = 0.70

# The search grid, in absolute units and **independent of what the closed form returns**. A bracket
# centred on the closed form's own answer would make the search partly circular: it would confirm
# that a crossing exists near the answer rather than find the crossing. 0.005 is the resolution the
# comparison is stated at, and it is one of the two terms in the tolerance below.
GRID_LOW, GRID_HIGH, GRID_STEP = 0.010, 0.500, 0.005

# 4 000 trials per grid point. The standard error of a power estimate near 0.80 is
# sqrt(0.8 * 0.2 / 4000) = 0.0063 in *power* units; the power curve near the crossing rises at
# roughly 3 power-units per unit of delta at these sizes, so that is about 0.002 in *delta* units.
TRIALS = 4000
SEED = 0x5EED_0101

# **The tolerance, stated and argued.** Three terms, added rather than combined in quadrature
# because the first is a hard quantisation and not a random error:
#
#   0.005  the grid step — the simulation cannot report a threshold between two grid points;
#   0.002  the Monte-Carlo standard error at the crossing, converted to delta units (above);
#   0.003  the normal approximation itself. The closed form is a continuous approximation to a
#          discrete test, and at n = 37 a binomial proportion moves in steps of 1/37 = 0.027, so a
#          simulated rejection rate crosses 0.80 in jumps. This term is the one that would have to
#          grow if the corpus ever got smaller, and it is named separately for that reason.
#
# Total 0.010. **This is not a free parameter.** The naive form it replaces differs from the
# variance-correct one by 0.060 at n = 37 — six times the tolerance — so a guard that admitted the
# old arithmetic would have to be loosened by an amount no one could write down and defend. The
# injection proving that is recorded in `docs/gates/v0.10.1-guard-demonstrations.md`.
TOLERANCE = 0.010

SIZES = (37, 120, 300)


class _Xorshift:
    """xorshift64\\*, chosen because it is **not** the generator `shadow_cv` uses.

    Sharing a PRNG would be sharing code and not sharing arithmetic, which is the distinction that
    matters here — but the cheapest way to be sure of that is to not share it either.
    """

    __slots__ = ("_s",)

    def __init__(self, seed: int) -> None:
        self._s = (seed & 0xFFFF_FFFF_FFFF_FFFF) or 0x9E37_79B9_7F4A_7C15

    def unit(self) -> float:
        """A float in [0, 1), from the top 53 bits."""
        s = self._s
        s ^= (s << 13) & 0xFFFF_FFFF_FFFF_FFFF
        s ^= s >> 7
        s ^= (s << 17) & 0xFFFF_FFFF_FFFF_FFFF
        self._s = s
        return ((s * 0x2545_F491_4F6C_DD1D & 0xFFFF_FFFF_FFFF_FFFF) >> 11) / float(1 << 53)


def _binomial_cdf(n: int, p: float) -> list[float]:
    """The exact CDF of `Binomial(n, p)`, in log space so `n = 300` does not overflow.

    Exact rather than approximated on purpose: an independent check that used a *normal*
    approximation to draw its samples would be re-importing the assumption the closed form makes,
    one layer down, and this file exists because that already happened once.
    """
    log_p = math.log(p) if p > 0.0 else -math.inf
    log_q = math.log1p(-p) if p < 1.0 else -math.inf
    cdf: list[float] = []
    total = 0.0
    for k in range(n + 1):
        log_pmf = (
            math.lgamma(n + 1)
            - math.lgamma(k + 1)
            - math.lgamma(n - k + 1)
            + (k * log_p if k else 0.0)
            + ((n - k) * log_q if n - k else 0.0)
        )
        total += math.exp(log_pmf)
        cdf.append(total)
    cdf[-1] = 1.0  # absorb the float error into the last cell so a draw can never fall off the end
    return cdf


def _simulated_power(n: int, p: float, delta: float, *, seed: int = SEED) -> float:
    """Fraction of `TRIALS` in which a two-sided pooled z-test separates `p` from `p + delta`.

    No shared arithmetic with `minimum_detectable_difference`: it draws, it tests, it counts.
    """
    lo, hi = _binomial_cdf(n, p), _binomial_cdf(n, min(1.0, p + delta))
    rng = _Xorshift(seed)
    rejects = 0
    for _ in range(TRIALS):
        a = bisect.bisect_left(lo, rng.unit())
        b = bisect.bisect_left(hi, rng.unit())
        pooled = (a + b) / (2 * n)
        se = math.sqrt(2.0 * pooled * (1.0 - pooled) / n)
        if se == 0.0:
            # Both arms all-zero or all-one. The test statistic is undefined; a difference of zero
            # is not a rejection and a difference cannot exist when both arms are identical, so
            # this contributes nothing either way.
            continue
        if abs(a / n - b / n) / se > Z_ALPHA:
            rejects += 1
    return rejects / TRIALS


def _simulated_threshold(n: int, p: float = P) -> float:
    """The smallest grid delta whose simulated power reaches 0.80."""
    steps = round((GRID_HIGH - GRID_LOW) / GRID_STEP) + 1
    for i in range(steps):
        delta = GRID_LOW + i * GRID_STEP
        if _simulated_power(n, p, delta) >= 0.80:
            return delta
    raise AssertionError(f"no grid point reached 80 % power at n={n}; the grid is too narrow")


def _naive(n: int, p: float = P) -> float:
    """The v0.10.0 form: **both** arms carry the base rate's variance. Kept as the control."""
    return (Z_ALPHA + 0.841621) * math.sqrt(2.0 * p * (1.0 - p) / n)


# --- the pin ------------------------------------------------------------------------------------


def test_the_closed_form_agrees_with_an_independent_power_simulation() -> None:
    """**The claim.** Two methods, no shared arithmetic, the same answer at three sizes.

    This is the test v0.10.0 did not have. It had a Monte-Carlo, and it reasoned about the number
    the Monte-Carlo produced — but nothing failed when the two disagreed, so the disagreement became
    a paragraph in a security review instead of a red test.
    """
    for n in SIZES:
        closed = minimum_detectable_difference(n, P)
        simulated = _simulated_threshold(n)
        assert abs(closed - simulated) <= TOLERANCE, (
            f"n={n}: closed form {closed:.4f} against simulation {simulated:.4f}, "
            f"difference {abs(closed - simulated):.4f} exceeds the stated tolerance {TOLERANCE}"
        )


def test_the_simulation_rejects_the_form_this_release_replaced() -> None:
    """**CONTROL, and the one that makes the tolerance mean something.**

    If the simulation agreed with the naive form too, it would be agreeing with everything and the
    test above would be evidence of nothing.

    **It does not reject the naive form everywhere, and that is the finding rather than a
    weakness.** Measured gaps: 0.0585 at n = 37, 0.0157 at n = 120, **0.0048 at n = 300**
    — inside the tolerance. The two forms converge as `n` grows, because the second arm's variance
    approaches the first's, and that convergence *is* the variance term's signature. It is also
    exactly why v0.10.0 saw agreement at large `n` and disagreement at small `n` and concluded the
    plan was wrong: the regime where the naive form is nearly right is the regime it was checked in.

    So the discrimination is asserted where the corpus actually lives — **37 incidents** — and the
    convergence at 300 is asserted as convergence rather than quietly tolerated.
    """
    gaps = {n: abs(_naive(n, P) - _simulated_threshold(n)) for n in SIZES}
    assert gaps[37] > 5 * TOLERANCE, (
        f"at n=37 — this project's own corpus size — the naive form must be far outside the "
        f"tolerance; measured {gaps[37]:.4f} against a tolerance of {TOLERANCE}. If this ever "
        f"narrows, the pin above has stopped discriminating and its tolerance needs re-deriving "
        f"rather than re-using."
    )
    assert gaps[120] > TOLERANCE, f"the naive form must still be rejected at n=120: {gaps}"
    assert gaps[300] < gaps[120] < gaps[37], (
        f"the gap must CLOSE as n grows, which is what makes it the variance term rather than an "
        f"arithmetic slip: {gaps}"
    )


def test_the_simulation_is_deterministic_across_two_runs() -> None:
    """Prime directive 9. A pin whose own number moved would be a flaky gate, not a guard."""
    assert _simulated_threshold(37) == _simulated_threshold(37)
    assert _simulated_power(120, P, 0.15) == _simulated_power(120, P, 0.15)


# --- the properties the correction has to preserve ------------------------------------------------


def test_the_threshold_falls_as_the_corpus_grows() -> None:
    """**CONTROL.** A fixed-point solve that failed to converge could return anything; a threshold
    that did not fall with `n` would not be a detection threshold at all."""
    values = [minimum_detectable_difference(n, P) for n in (12, 37, 120, 300, 1000, 5000)]
    assert all(a > b for a, b in pairwise(values)), values


def test_the_correction_lowers_the_threshold_and_by_more_at_small_n() -> None:
    """**The signature of the variance term**, and the reason A2's conclusion runs opposite to #142.

    The naive form assumes both arms carry `p(1-p)`. The second arm sits at `p + delta`, so the
    over-statement is largest exactly where `delta` is largest — at small `n`. A correction that
    moved every size by the same amount would be an arithmetic slip rather than this.
    """
    gaps = [(n, _naive(n, P) - minimum_detectable_difference(n, P)) for n in (12, 37, 120, 300)]
    assert all(gap > 0 for _n, gap in gaps), f"the correction must LOWER the threshold: {gaps}"
    assert all(a[1] > b[1] for a, b in pairwise(gaps)), (
        f"the gap must shrink as n grows; that is what makes it the variance term: {gaps}"
    )


def test_the_corrected_form_no_longer_disagrees_with_the_ratified_table() -> None:
    """§3.1's registered table, which v0.10.0 reported as unreproducible below `n = 120`.

    The plan is **not** edited and nothing registered depends on these numbers — the table is a
    reported quantity and a verdict trigger, never a floor. This asserts what changed: at the three
    sizes where a threshold exists at all, the closed form now lands within a rounding step of the
    registered figure, where the naive one was 5 p.p. high at 37.

    `n = 12` is deliberately absent and has its own test below, because at 12 incidents the answer
    is not a number.
    """
    registered = {37: 0.25, 120: 0.16, 300: 0.10}
    for n, table in registered.items():
        assert abs(minimum_detectable_difference(n, P) - table) <= 0.02, (
            f"n={n}: {minimum_detectable_difference(n, P):.3f} against a registered {table}"
        )


def test_at_twelve_incidents_no_attainable_difference_reaches_eighty_percent_power() -> None:
    """**A2's second finding, and it is about the parameter space rather than about arithmetic.**

    §3.1 registers **0.42** at `n = 12`. At a base rate of `p = 0.70` a difference of 0.42 puts the
    second arm at **1.12**, which is not a proportion. The largest difference that exists at this
    base rate is 0.30, and the simulation puts its power at **0.519** — so at 12 incidents there is
    no detectable difference at all, not a large one.

    The closed form says the same thing in the only way a continuous formula can: the `q <= 1` clamp
    binds and it returns 0.371, a value above every attainable difference. **Both methods agree that
    12 incidents resolve nothing**, which is a stronger version of the plan's own conclusion and not
    a contradiction of it — but it means the registered 0.42 is not a threshold that was too
    optimistic, it is a threshold that does not exist.

    Recorded rather than repaired: the plan is ratified and hash-guarded, the figure is a reported
    quantity and never a floor, and `PREREGISTRATION-0.10.0.md` is not edited by this release.
    """
    assert _simulated_power(12, P, 0.30) < 0.80, (
        "the largest attainable difference at p=0.70 must NOT reach 80 % power at n=12; if "
        "it does, this finding is wrong and the registered 0.42 needs re-examining, not this test"
    )
    assert P + 0.42 > 1.0, "the registered figure must be outside the parameter space, or say so"
    assert minimum_detectable_difference(12, P) > 0.30, (
        "the closed form must report a threshold above every attainable difference, which is how a "
        "continuous approximation says 'nothing is detectable here'"
    )
    # CONTROL: the same simulation at a size where a threshold DOES exist must find one, or the
    # assertion above would hold for a simulation that could never reach 80 % power at all.
    assert _simulated_power(300, P, 0.15) >= 0.80


def test_a_degenerate_corpus_returns_the_widest_possible_threshold() -> None:
    """`n <= 0` is unreachable from any call site and returns 1.0 rather than dividing by zero:
    *no difference is detectable* is the honest answer for a corpus with no incidents in it."""
    assert minimum_detectable_difference(0) == 1.0
    assert minimum_detectable_difference(-1) == 1.0
