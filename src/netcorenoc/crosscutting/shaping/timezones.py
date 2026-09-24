"""Time zones an operator actually schedules into, and the zone the appliance stores (v0.21.0).

**D2: mandatory, with no new Python dependency.** `zoneinfo` is in the standard library and the
IANA database it reads is an operating-system package, so this module adds nothing to
`pyproject.toml` — see `Dockerfile`, where one `apt-get` line installs `tzdata` in the final image,
and ADR #363 for why an OS package is not a Python runtime dependency.

## The distinction this module exists for: a city is not a zone

Three of the cities the maintainer named have **no zone of their own**, checked against this
build's own `zoneinfo`:

    America/Washington   INVALID  ->  America/New_York
    America/Brasilia     INVALID  ->  America/Sao_Paulo
    Asia/Beijing         INVALID  ->  America/Shanghai   (Asia/Shanghai)

So the picker **shows cities and stores canonical zones**. An operator searches *"Brasília"* and
the window is stored as `America/Sao_Paulo`, which is the rule that governs it — and a label
stored in place of its zone is one of the injections this release runs red (§10).

## What is deliberately not here

The four legacy aliases an earlier check found unresolvable on a stock host — `PRC`, `ROC`, `ROK`
and `UCT` — are **backward links**, shipped by Debian in a separate `tzdata-legacy` package. They
are not installed and they are not curated: every entry below is a canonical zone, so the appliance
never needs one. `search()` will still return them on a host that has them, because it enumerates
what that host can resolve rather than what this file believes.

## The self-check, and why it is not a test

`tests/test_timezones.py` asserts this list resolves **on the build machine**. That is the trap
`docs/findings.md` F131 names one layer up: the testbed's NE image shipped unbuildable because
`docker compose config` never reads `.dockerignore`, and a zone list validated on the build machine
is the same shape of mistake. :func:`timezone_selfcheck` runs in the **running appliance**,
against the `tzdata` that image actually has, and reports a curated entry that does not resolve
as an operator warning through the channel that already carries eight others.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

#: How many zones :func:`search_zones` returns. The console renders a combobox, not 598 rows.
SEARCH_LIMIT = 12


@dataclass(frozen=True)
class Zone:
    """One offerable zone: what an operator types, and what the appliance stores.

    `label` is a **city**, because that is what an operator knows about a site. `zone` is the
    canonical IANA identifier, because that is what carries the rule — including the DST
    transitions a window may span, which is the whole reason a stored offset would be wrong.
    """

    label: str
    zone: str
    country: str
    curated: bool = True


#: **The curated list.** Cities an operator in this product's market actually schedules work into,
#: each mapped to the canonical zone that governs it. Ordered by region so the console's first
#: page is browsable rather than alphabetical; `search()` re-orders by match quality.
#:
#: Every `zone` here is canonical — no backward links — and `selfcheck()` proves it resolves on
#: the host that is running, not on the host that built it.
CURATED: tuple[Zone, ...] = (
    # South America — the operator's own region, first
    Zone("Brasília", "America/Sao_Paulo", "BR"),
    Zone("São Paulo", "America/Sao_Paulo", "BR"),
    Zone("Rio de Janeiro", "America/Sao_Paulo", "BR"),
    Zone("Manaus", "America/Manaus", "BR"),
    Zone("Recife", "America/Recife", "BR"),
    Zone("Fortaleza", "America/Fortaleza", "BR"),
    Zone("Belém", "America/Belem", "BR"),
    Zone("Buenos Aires", "America/Argentina/Buenos_Aires", "AR"),
    Zone("Santiago", "America/Santiago", "CL"),
    Zone("Montevideo", "America/Montevideo", "UY"),
    Zone("Asunción", "America/Asuncion", "PY"),
    Zone("La Paz", "America/La_Paz", "BO"),
    Zone("Lima", "America/Lima", "PE"),
    Zone("Bogotá", "America/Bogota", "CO"),
    Zone("Quito", "America/Guayaquil", "EC"),
    Zone("Caracas", "America/Caracas", "VE"),
    # North and Central America
    Zone("Washington", "America/New_York", "US"),
    Zone("New York", "America/New_York", "US"),
    Zone("Miami", "America/New_York", "US"),
    Zone("Atlanta", "America/New_York", "US"),
    Zone("Chicago", "America/Chicago", "US"),
    Zone("Dallas", "America/Chicago", "US"),
    Zone("Denver", "America/Denver", "US"),
    Zone("Phoenix", "America/Phoenix", "US"),
    Zone("Los Angeles", "America/Los_Angeles", "US"),
    Zone("San Francisco", "America/Los_Angeles", "US"),
    Zone("Seattle", "America/Los_Angeles", "US"),
    Zone("Anchorage", "America/Anchorage", "US"),
    Zone("Honolulu", "Pacific/Honolulu", "US"),
    Zone("Toronto", "America/Toronto", "CA"),
    Zone("Montréal", "America/Toronto", "CA"),
    Zone("Winnipeg", "America/Winnipeg", "CA"),
    Zone("Calgary", "America/Edmonton", "CA"),
    Zone("Vancouver", "America/Vancouver", "CA"),
    Zone("Mexico City", "America/Mexico_City", "MX"),
    Zone("Panama", "America/Panama", "PA"),
    Zone("San José", "America/Costa_Rica", "CR"),
    # Europe
    Zone("London", "Europe/London", "GB"),
    Zone("Dublin", "Europe/Dublin", "IE"),
    Zone("Lisbon", "Europe/Lisbon", "PT"),
    Zone("Madrid", "Europe/Madrid", "ES"),
    Zone("Paris", "Europe/Paris", "FR"),
    Zone("Brussels", "Europe/Brussels", "BE"),
    Zone("Amsterdam", "Europe/Amsterdam", "NL"),
    Zone("Berlin", "Europe/Berlin", "DE"),
    Zone("Frankfurt", "Europe/Berlin", "DE"),
    Zone("Zürich", "Europe/Zurich", "CH"),
    Zone("Milan", "Europe/Rome", "IT"),
    Zone("Rome", "Europe/Rome", "IT"),
    Zone("Vienna", "Europe/Vienna", "AT"),
    Zone("Prague", "Europe/Prague", "CZ"),
    Zone("Warsaw", "Europe/Warsaw", "PL"),
    Zone("Stockholm", "Europe/Stockholm", "SE"),
    Zone("Oslo", "Europe/Oslo", "NO"),
    Zone("Helsinki", "Europe/Helsinki", "FI"),
    Zone("Athens", "Europe/Athens", "GR"),
    Zone("Bucharest", "Europe/Bucharest", "RO"),
    Zone("Kyiv", "Europe/Kyiv", "UA"),
    Zone("Istanbul", "Europe/Istanbul", "TR"),
    Zone("Moscow", "Europe/Moscow", "RU"),
    # Middle East and Africa
    Zone("Riyadh", "Asia/Riyadh", "SA"),
    Zone("Jeddah", "Asia/Riyadh", "SA"),
    Zone("Dubai", "Asia/Dubai", "AE"),
    Zone("Abu Dhabi", "Asia/Dubai", "AE"),
    Zone("Doha", "Asia/Qatar", "QA"),
    Zone("Kuwait City", "Asia/Kuwait", "KW"),
    Zone("Manama", "Asia/Bahrain", "BH"),
    Zone("Muscat", "Asia/Muscat", "OM"),
    Zone("Baghdad", "Asia/Baghdad", "IQ"),
    Zone("Tehran", "Asia/Tehran", "IR"),
    Zone("Tel Aviv", "Asia/Jerusalem", "IL"),
    Zone("Amman", "Asia/Amman", "JO"),
    Zone("Beirut", "Asia/Beirut", "LB"),
    Zone("Cairo", "Africa/Cairo", "EG"),
    Zone("Casablanca", "Africa/Casablanca", "MA"),
    Zone("Algiers", "Africa/Algiers", "DZ"),
    Zone("Lagos", "Africa/Lagos", "NG"),
    Zone("Accra", "Africa/Accra", "GH"),
    Zone("Nairobi", "Africa/Nairobi", "KE"),
    Zone("Addis Ababa", "Africa/Addis_Ababa", "ET"),
    Zone("Luanda", "Africa/Luanda", "AO"),
    Zone("Maputo", "Africa/Maputo", "MZ"),
    Zone("Johannesburg", "Africa/Johannesburg", "ZA"),
    Zone("Cape Town", "Africa/Johannesburg", "ZA"),
    # Asia and Oceania
    Zone("Karachi", "Asia/Karachi", "PK"),
    Zone("Mumbai", "Asia/Kolkata", "IN"),
    Zone("New Delhi", "Asia/Kolkata", "IN"),
    Zone("Bengaluru", "Asia/Kolkata", "IN"),
    Zone("Colombo", "Asia/Colombo", "LK"),
    Zone("Dhaka", "Asia/Dhaka", "BD"),
    Zone("Kathmandu", "Asia/Kathmandu", "NP"),
    Zone("Yangon", "Asia/Yangon", "MM"),
    Zone("Bangkok", "Asia/Bangkok", "TH"),
    Zone("Hanoi", "Asia/Ho_Chi_Minh", "VN"),
    Zone("Ho Chi Minh City", "Asia/Ho_Chi_Minh", "VN"),
    Zone("Jakarta", "Asia/Jakarta", "ID"),
    Zone("Kuala Lumpur", "Asia/Kuala_Lumpur", "MY"),
    Zone("Singapore", "Asia/Singapore", "SG"),
    Zone("Manila", "Asia/Manila", "PH"),
    Zone("Beijing", "Asia/Shanghai", "CN"),
    Zone("Shanghai", "Asia/Shanghai", "CN"),
    Zone("Shenzhen", "Asia/Shanghai", "CN"),
    Zone("Hong Kong", "Asia/Hong_Kong", "HK"),
    Zone("Taipei", "Asia/Taipei", "TW"),
    Zone("Seoul", "Asia/Seoul", "KR"),
    Zone("Tokyo", "Asia/Tokyo", "JP"),
    Zone("Osaka", "Asia/Tokyo", "JP"),
    Zone("Perth", "Australia/Perth", "AU"),
    Zone("Adelaide", "Australia/Adelaide", "AU"),
    Zone("Brisbane", "Australia/Brisbane", "AU"),
    Zone("Melbourne", "Australia/Melbourne", "AU"),
    Zone("Sydney", "Australia/Sydney", "AU"),
    Zone("Auckland", "Pacific/Auckland", "NZ"),
    # The one entry that is not a city, and the only zone with no politics in it.
    Zone("UTC", "UTC", ""),
)

#: The zone a window falls back to when nothing else is known. Not the host's local zone: a NOC
#: appliance's own clock is an accident of where it was racked, and inheriting it would make the
#: same request mean two things on two installs.
DEFAULT_ZONE = "UTC"


def resolves(zone: str) -> bool:
    """Can **this host** load that zone? The only question worth asking about a zone string.

    `ZoneInfo` raises `ZoneInfoNotFoundError` for an unknown key and `ValueError` for one that is
    not a well-formed key at all (an absolute path, a `..` segment). Both mean the same thing here
    and both are refused, which is also what keeps a caller-supplied string off the filesystem.
    """
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def fold(text: str) -> str:
    """Case- and **accent**-blind form of a string, for search only (v0.21.0).

    `"Brasília"` and `"Brasilia"` fold to the same thing, and so do `"São Paulo"` / `"Sao Paulo"`
    and `"Zürich"` / `"Zurich"`. NFKD decomposes a precomposed letter into its base plus a
    combining mark; dropping category `Mn` leaves the base.

    **Found by the live pass, not by a test** (F147). The docstring on :func:`search_zones` claimed
    the search was *"case- and accent-blind for the ASCII cases that matter (`Sao Paulo` finds
    `São Paulo` because the identifier matches)"* — and that reasoning holds only where the zone
    identifier happens to carry the ASCII spelling. **Brasília's identifier is
    `America/Sao_Paulo`**, so an operator on an ASCII keyboard typing the city the maintainer
    actually named got an empty list. The claim was true of its example and false of the case the
    feature exists for.

    Used for comparison only; what is **shown** is the label as written and what is **stored** is
    the canonical identifier. Folding is not a rename.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(ch)
    )


def _rank(zone: Zone, needle: str) -> tuple[int, int, str]:
    """Sort key for a search hit: exact label, then prefix, then contained; curated first."""
    label = fold(zone.label)
    if label == needle:
        quality = 0
    elif label.startswith(needle):
        quality = 1
    elif needle in label:
        quality = 2
    else:
        quality = 3  # matched on the zone identifier rather than the city
    return (quality, 0 if zone.curated else 1, zone.label)


def search_zones(query: str, limit: int = SEARCH_LIMIT) -> list[Zone]:
    """Curated cities first, then **every zone this host can resolve** (D2's second half).

    An empty query returns the head of the curated list, which is what the console shows before
    anybody types. A query matches a city label or a zone identifier, **case- and accent-blind**:
    `Brasilia` finds `Brasília`, which it did not until the live pass tried it — see :func:`fold`
    and `docs/findings.md` F147.

    The uncurated half is enumerated from :func:`zoneinfo.available_timezones`, so an operator on
    a host with a fuller database gets what that host has — never what this file guessed it had.
    """
    needle = fold(query.strip())
    if not needle:
        return list(CURATED[:limit])
    hits = [zone for zone in CURATED if needle in fold(zone.label) or needle in fold(zone.zone)]
    seen = {zone.zone for zone in hits}
    try:
        catalog = sorted(available_timezones())
    except Exception:  # pragma: no cover - a host with no database at all; selfcheck reports it
        catalog = []
    for identifier in catalog:
        if len(hits) >= limit * 3:
            break
        if identifier in seen:
            continue
        if needle in fold(identifier):
            hits.append(Zone(identifier, identifier, "", curated=False))
            seen.add(identifier)
    hits.sort(key=lambda zone: _rank(zone, needle))
    return hits[:limit]


def offset_seconds(zone: str, instant: float) -> int:
    """The UTC offset that zone has **at that instant**, in seconds; 0 if it cannot be loaded.

    At that instant, not in general: a window spanning a DST transition has two offsets, and a
    stored one would make the second half of it an hour wrong. The API stores RFC 3339 instants
    with an explicit offset for exactly this reason, and this function exists so the console can
    render the site's wall clock without inventing arithmetic of its own.
    """
    try:
        moment = datetime.fromtimestamp(instant, ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError, OSError, OverflowError):
        return 0
    delta = moment.utcoffset()
    return int(delta.total_seconds()) if delta is not None else 0


def offset_label(zone: str, instant: float) -> str:
    """`UTC-03:00` for that zone at that instant — the string the console puts beside a clock."""
    total = offset_seconds(zone, instant)
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def wall_clock(zone: str, instant: float) -> str:
    """`2026-09-21T10:00:00-03:00` — the instant as that zone's wall clock, RFC 3339.

    Falls back to UTC on an unloadable zone rather than raising: this is a rendering helper on a
    read path, and a console that cannot draw a clock must still draw the rest of the card.
    """
    try:
        moment = datetime.fromtimestamp(instant, ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError, OSError, OverflowError):
        moment = datetime.fromtimestamp(instant, UTC)
    return moment.isoformat(timespec="seconds")


def timezone_selfcheck() -> list[str]:
    """Operator warnings for curated zones **this running appliance** cannot resolve.

    Runs at startup, in the image, against the `tzdata` that image has — never against the build
    machine's, which is the whole point (see the module docstring). Silent when everything
    resolves, which is the ordinary state; a warning list that always holds an entry is a warning
    list nobody reads.

    One warning for the whole set rather than one per zone: an image with no time-zone database has
    118 broken entries, and 118 identical warnings would bury the seven other things the banner is
    trying to say. The count and the first few names are what an operator acts on.
    """
    missing = sorted({zone.zone for zone in CURATED if not resolves(zone.zone)})
    if not missing:
        return []
    shown = ", ".join(missing[:5])
    more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
    return [
        f"{len(missing)} of the {len({z.zone for z in CURATED})} offered time zones cannot be "
        f"resolved on this host ({shown}{more}). Maintenance windows in those zones cannot be "
        "scheduled correctly. Install the operating system's time-zone database — on Debian and "
        "Ubuntu that is the `tzdata` package, which the shipped container image installs."
    ]
