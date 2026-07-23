from __future__ import annotations

from opticorr import known_oids


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
