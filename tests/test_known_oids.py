from __future__ import annotations

from netcorenoc.ingest import known_oids


def test_vendor_of_known_enterprises() -> None:
    assert known_oids.vendor_of("1.3.6.1.4.1.1271.2.1.1") == "Ciena"
    assert known_oids.vendor_of("1.3.6.1.4.1.2011.5.25.1") == "Huawei"
    assert known_oids.vendor_of("1.3.6.1.4.1.9.9.41.2.0.1") == "Cisco Systems"


def test_vendor_of_unknown_enterprise_is_honest() -> None:
    assert known_oids.vendor_of("1.3.6.1.4.1.999999.1.2") == "enterprise-999999"


def test_vendor_of_non_enterprise_oid() -> None:
    assert known_oids.vendor_of("1.3.6.1.6.3.1.1.5.3") is None
    assert known_oids.vendor_of("1.3.6.1.4.1.") is None
    assert known_oids.vendor_of("1.3.6.1.4.1.bogus.2") is None


def test_standard_trap_names() -> None:
    assert known_oids.trap_name("1.3.6.1.6.3.1.1.5.1") == "coldStart"
    assert known_oids.trap_name("1.3.6.1.6.3.1.1.5.3") == "linkDown"
    assert known_oids.trap_name("1.3.6.1.4.1.9.9.41.2.0.1") is None


def test_varbind_names_exact_and_column() -> None:
    assert known_oids.varbind_name("1.3.6.1.2.1.1.3.0") == "sysUpTime"
    assert known_oids.varbind_name("1.3.6.1.2.1.2.2.1.1.7") == "ifIndex"
    assert known_oids.varbind_name("1.3.6.1.4.1.9.9.1.1") is None


def test_clear_pair_seeds_link_down_up() -> None:
    assert known_oids.CLEAR_PAIR_SEEDS["1.3.6.1.6.3.1.1.5.3"] == "1.3.6.1.6.3.1.1.5.4"


# --------------------------------------------------------------------------------------------
# The standard read (v0.17.1, DECISIONS #337). The trap's own severity word, in X.733's
# vocabulary. Not an inference, and not a claim this repository makes on any vendor's behalf.
# --------------------------------------------------------------------------------------------


def test_the_standard_read_takes_the_word_the_trap_carried() -> None:
    """The release's whole first movement, at the layer that produces it.

    The OID is returned alongside the token because the console has to say *where* the severity
    came from: an operator who disagrees needs to know which varbind to look at on their own
    device before they overrule it.

    **The OID is a pointer, not a citation, and this test does not name the column.**
    `1.3.6.1.2.1.118.1.2.2.1.4` is under RFC 3877's ALARM-MIB arc and is what the lab's NEs emit,
    but which `alarmActiveEntry` column `.4` is cannot be checked from this build environment —
    `www.rfc-editor.org` answers 403 to the egress proxy. Naming it here would be an assertion
    nobody verified, which is the defect #337 exists to close. It does not matter to the
    mechanism: `standard_severity` is keyed on the *value*, so it reads the word at whatever OID
    the device chose and would work identically if the lab moved it tomorrow.
    """
    lab_trap = [
        {"oid": "1.3.6.1.2.1.1.3.0", "kind": "int", "value": 12345},
        {"oid": "1.3.6.1.2.1.118.1.2.2.1.4", "kind": "str", "value": "critical"},
    ]
    assert known_oids.standard_severity(lab_trap) == (
        "critical",
        0,
        "1.3.6.1.2.1.118.1.2.2.1.4",
    )


def test_a_trap_that_carries_no_severity_word_is_not_given_one() -> None:
    """**No fabricated severity.** The prime directive, at its cheapest test.

    A trap whose varbinds hold an interface index and a description says nothing about how
    serious it is, and the honest answer is None — which the census counts as `unplaced` and the
    console renders `—`.
    """
    assert known_oids.standard_severity([]) is None
    assert (
        known_oids.standard_severity(
            [
                {"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": 7},
                {"oid": "1.3.6.1.2.1.2.2.1.2.7", "kind": "str", "value": "GigabitEthernet0/7"},
            ]
        )
        is None
    )


def test_the_most_severe_word_wins_not_the_first_one_seen() -> None:
    """A trap reporting both `cleared` and `major` is reporting something about both, and
    under-reporting a fault is the worse error. The ordering is X.733's, not one invented here."""
    both = [
        {"oid": "1.3.6.1.4.1.2011.1.1", "kind": "str", "value": "cleared"},
        {"oid": "1.3.6.1.4.1.2011.1.2", "kind": "str", "value": "major"},
    ]
    assert known_oids.standard_severity(both) == ("major", 1, "1.3.6.1.4.1.2011.1.2")
    assert known_oids.standard_severity(list(reversed(both))) == (
        "major",
        1,
        "1.3.6.1.4.1.2011.1.2",
    ), "the answer depended on varbind order, so two identical traps could disagree"


def test_a_non_string_varbind_is_stepped_over_and_never_coerced() -> None:
    """SNMP integers are severities on plenty of devices, and this deliberately does not read
    them: `2` means nothing without that NE's whole rank set (F99), which is what the *learned*
    arm exists to establish. Coercing it here would be exactly the fabrication #337 forbids."""
    assert (
        known_oids.standard_severity(
            [
                {"oid": "1.3.6.1.4.1.2011.1.1", "kind": "int", "value": 2},
                {"oid": "1.3.6.1.4.1.2011.1.2", "kind": "str", "value": None},
                {"oid": "1.3.6.1.4.1.2011.1.3", "kind": "str"},
            ]
        )
        is None
    )


def test_the_word_is_matched_however_the_device_spelled_its_case() -> None:
    """`MAJOR`, `Major` and ` major ` are one word. The token comes back normalised so the
    console never has to decide which spelling to show."""
    for spelling in ("MAJOR", "Major", " major ", "mAjOr"):
        got = known_oids.standard_severity([{"oid": "1.3.6.1.4.1.1.1", "value": spelling}])
        assert got is not None and got[:2] == ("major", 1), f"{spelling!r} was not read as major"


def test_every_bundled_table_carries_a_source_the_reader_can_check() -> None:
    """**A derived guard, not a list** (F92/F98/F112/F121). The subject is every public-data
    table this module ships, found by inspecting the module — so a table added in v0.18.0 with no
    citation fails here without anyone remembering to add it to a list.

    This is the guard #337 exists for. `SEVERITY_VOCAB` shipped for eight releases with no
    attribution at all, which made standard knowledge look like a convenience this repository
    invented — the small dishonesty that a citation closes. Written as a list of the tables known
    in v0.17.1 it would have covered one, because that is the one anybody was thinking about;
    derived, it found four more on its first run.

    **The citation must be a constant, not a comment.** A comment is invisible to the appliance
    and cannot be shown to an operator asking which standard a `standard` severity came from.

    **The subject is derived and the citations are one mapping**, so neither side is a list that
    has to be maintained: a table added to the module appears here automatically, and the only
    thing its author has to write is the `BUNDLED_SOURCES` entry that makes this pass.
    """
    members = vars(known_oids)
    tables = {
        name
        for name, value in members.items()
        if name.isupper()
        and name != "BUNDLED_SOURCES"
        and isinstance(value, dict | frozenset | set)
    }
    assert len(tables) >= 5, (
        f"the derivation found only {sorted(tables)}; it is supposed to find every bundled table "
        "in the module, and a guard whose subject has quietly emptied covers nothing"
    )
    uncited = sorted(
        name
        for name in tables
        if not isinstance(known_oids.BUNDLED_SOURCES.get(name), str)
        or not known_oids.BUNDLED_SOURCES[name].strip()
    )
    assert not uncited, (
        f"bundled table(s) {uncited} ship public data with no stated source. Add a non-empty "
        f"`BUNDLED_SOURCES` entry for each, naming the standard or registry the rows came from — "
        f"a table nobody can check is a claim, not data."
    )
    orphans = sorted(set(known_oids.BUNDLED_SOURCES) - tables)
    assert not orphans, (
        f"`BUNDLED_SOURCES` cites {orphans}, which is not a table in this module. A citation for "
        "something that no longer exists is how a stale source outlives the data it described."
    )
