"""D2: a city is not a zone, and a list validated on the build machine is not validated.

The measurement that shaped this module, run here as assertions: three of the cities the
maintainer named — Washington, Brasília, Beijing — **have no IANA zone of their own**, so the
picker shows cities and stores canonical zones.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from netcorenoc.crosscutting.shaping import timezones as tz

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_three_cities_that_have_no_zone_of_their_own() -> None:
    """**The measurement, re-run.** If any of these ever becomes a real zone, this fails loudly
    and the curated entry can be simplified — which is the only way anyone would find out."""
    for invalid in ("America/Washington", "America/Brasilia", "Asia/Beijing"):
        try:
            ZoneInfo(invalid)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        raise AssertionError(f"{invalid} resolves now; the curated mapping can be simplified")

    for city, zone in (
        ("Washington", "America/New_York"),
        ("Brasília", "America/Sao_Paulo"),
        ("Beijing", "Asia/Shanghai"),
    ):
        hits = [entry for entry in tz.CURATED if entry.label == city]
        assert hits, f"{city} is not offered at all"
        assert hits[0].zone == zone, f"{city} maps to {hits[0].zone}, not {zone}"


def test_every_curated_zone_is_canonical_and_resolves_here() -> None:
    """On the build machine — which is **not** the claim that matters.

    `timezone_selfcheck()` runs in the running appliance against the `tzdata` that image has, and
    the Dockerfile installs it. This is the weaker, earlier check: a typo in the list below fails
    in CI rather than on an operator's first window.
    """
    unresolvable = sorted({entry.zone for entry in tz.CURATED if not tz.resolves(entry.zone)})
    assert not unresolvable, f"curated zones that do not resolve: {unresolvable}"
    assert not tz.timezone_selfcheck(), "the self-check disagrees with the direct check"


def test_no_curated_entry_is_a_deprecated_backward_link() -> None:
    """`PRC`, `ROC`, `ROK` and `UCT` are links Debian ships in a separate `tzdata-legacy`.

    The curated list uses canonical zones only, so the appliance never needs that package — which
    is why the Dockerfile installs `tzdata` and not `tzdata-legacy`, and why this asserts the
    property rather than the package.
    """
    legacy = {"PRC", "ROC", "ROK", "UCT", "Japan", "Singapore", "Hongkong", "Eire", "GB"}
    offenders = sorted({e.zone for e in tz.CURATED} & legacy)
    assert not offenders, f"curated entries using deprecated backward links: {offenders}"
    # Every canonical zone has a region prefix, except UTC itself.
    for entry in tz.CURATED:
        assert "/" in entry.zone or entry.zone == "UTC", f"{entry.zone} is not a canonical zone"


def test_the_dockerfile_installs_the_os_time_zone_database() -> None:
    """Prime directive 8, checked both ways: the OS package is there and `pyproject` is unchanged.

    `zoneinfo` is in the standard library; what a slim image lacks is the **data**. An `apt-get`
    line is not a Python runtime dependency, and the second assertion is what makes that claim
    checkable rather than merely stated.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "tzdata" in dockerfile, "the image ships no time-zone database, so D2 cannot work in it"
    assert "tzdata-legacy" not in dockerfile.replace("`tzdata-legacy` is deliberately NOT", ""), (
        "the deprecated backward links are being installed; no curated zone needs them"
    )

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = pyproject.split("[project.optional-dependencies]")[0]
    for forbidden in ("tzdata", "pytz", "dateutil", "pendulum", "arrow"):
        assert forbidden not in runtime, (
            f"{forbidden} was added as a Python runtime dependency; D2 says no new one, and the "
            "operating system's package is how the data gets in"
        )


def test_a_window_across_a_dst_transition_is_computed_from_the_zone() -> None:
    """§10's injection, at the layer that would get it wrong.

    An appliance that stored an offset instead of a zone would make the second half of a window
    across a transition an hour wrong. Brazil abolished DST in 2019, so the transition is taken
    where one still exists.
    """
    ny = ZoneInfo("America/New_York")
    spring = datetime(2026, 3, 8, 1, tzinfo=ny).timestamp()
    spring_end = datetime(2026, 3, 8, 5, tzinfo=ny).timestamp()
    assert spring_end - spring == 3 * 3600, "the spring-forward hour was not skipped"
    assert tz.offset_label("America/New_York", spring) == "UTC-05:00"
    assert tz.offset_label("America/New_York", spring_end) == "UTC-04:00", (
        "the offset is being read once and reused across the window rather than at each instant"
    )


def test_the_site_time_and_your_time_line_this_exists_for() -> None:
    """II.5's UX, as arithmetic: *"10:00 Riyadh · 04:00 your time (Brasília)"*."""
    riyadh_10am = datetime(2026, 5, 14, 10, tzinfo=ZoneInfo("Asia/Riyadh")).timestamp()
    assert tz.wall_clock("Asia/Riyadh", riyadh_10am).startswith("2026-05-14T10:00:00")
    assert tz.wall_clock("America/Sao_Paulo", riyadh_10am).startswith("2026-05-14T04:00:00")
    assert tz.offset_label("Asia/Riyadh", riyadh_10am) == "UTC+03:00"
    assert tz.offset_label("America/Sao_Paulo", riyadh_10am) == "UTC-03:00"


def test_search_puts_curated_cities_first_and_still_finds_everything_else() -> None:
    """D2's two halves: a curated list for the operator, every resolvable zone for the rest."""
    assert [z.label for z in tz.search_zones("Bras")][:1] == ["Brasília"]
    assert tz.search_zones("Riyadh")[0].zone == "Asia/Riyadh"
    # An uncurated zone this host can resolve is still reachable.
    uncurated = tz.search_zones("Reykjavik")
    assert uncurated and uncurated[0].zone == "Atlantic/Reykjavik"
    assert uncurated[0].curated is False
    # An empty query is the console's first page.
    assert len(tz.search_zones("")) == tz.SEARCH_LIMIT


def test_a_hostile_zone_string_never_reaches_the_filesystem() -> None:
    """`ZoneInfo` takes a key, and a key that is a path is refused rather than followed."""
    for hostile in ("../../etc/passwd", "/etc/passwd", "", "..", "America/../../etc/shadow"):
        assert not tz.resolves(hostile), f"{hostile!r} was accepted as a time zone"


def test_the_self_check_reports_rather_than_raising() -> None:
    """An image with no time-zone database must still boot and still ingest traps.

    The appliance degrades to *"windows cannot be scheduled and here is why"*, never to *"the
    process did not start"* — a NOC trap sink that refuses to run because of a calendar feature
    would be the worse failure by a wide margin.
    """
    broken = tz.Zone("Nowhere", "Not/AZone", "")
    saved = tz.CURATED
    try:
        tz.CURATED = (*saved, broken)
        warnings = tz.timezone_selfcheck()
        assert len(warnings) == 1, warnings
        assert "Not/AZone" in warnings[0]
        assert "tzdata" in warnings[0], "the warning does not say how to fix it"
    finally:
        tz.CURATED = saved


def test_the_three_named_cities_are_found_from_an_ascii_keyboard() -> None:
    """**F147**, found by the live pass rather than by this file.

    The search was case-blind and not accent-blind, and the docstring said otherwise on the
    strength of one example: `Sao Paulo` finds `São Paulo` *because the zone identifier carries the
    ASCII spelling*. **Brasília's identifier is `America/Sao_Paulo`**, so the city the maintainer
    named most often returned an empty list to anyone typing it without the accent — which is
    everyone on a US or UK keyboard.

    The control is the accented spelling: without it this passes on a search that had stopped
    matching labels altogether and was finding everything through the identifier.
    """
    from netcorenoc.crosscutting.shaping import timezones

    for typed, expected in (
        ("Brasilia", "America/Sao_Paulo"),
        ("Brasília", "America/Sao_Paulo"),
        ("Washington", "America/New_York"),
        ("Beijing", "Asia/Shanghai"),
        ("Zurich", "Europe/Zurich"),
        ("Zürich", "Europe/Zurich"),
    ):
        hits = timezones.search_zones(typed, limit=5)
        assert hits, f"{typed!r} found no zone at all"
        assert hits[0].zone == expected, (
            f"{typed!r} ranked {hits[0].zone} first, not {expected} — an operator typing the city "
            f"they think in must get the zone that governs it: {[(h.label, h.zone) for h in hits]}"
        )


def test_folding_is_for_comparison_only_and_never_renames_anything() -> None:
    """The control for the fix. Folding what is **shown** would be a worse defect than the one it
    repaired: an operator searching `Brasilia` must still see `Brasília` on the card, because that
    is the city's name, and the appliance must still store `America/Sao_Paulo`."""
    from netcorenoc.crosscutting.shaping import timezones

    (hit,) = [z for z in timezones.search_zones("Brasilia", limit=5) if z.zone.endswith("Paulo")]
    assert hit.label == "Brasília", f"the label was folded on its way to the screen: {hit.label!r}"
    assert timezones.fold("Brasília") == "brasilia"
    assert timezones.fold("São Paulo") == "sao paulo"
