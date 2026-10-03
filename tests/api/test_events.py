from __future__ import annotations

import pytest
from pydantic import ValidationError

from netcorenoc.ingest.events import TrapEvent, Varbind


def test_fingerprint_is_device_class_instance() -> None:
    e = TrapEvent(device="10.0.0.1", trap_oid="1.3.6.1.4.1.9.1", instance="if7", ts=1.0)
    assert e.fingerprint == ("10.0.0.1", "1.3.6.1.4.1.9.1", "if7")


def test_instance_defaults_to_empty() -> None:
    e = TrapEvent(device="10.0.0.1", trap_oid="1.3.6.1.4.1.9.1", ts=1.0)
    assert e.instance == ""
    assert e.varbinds == []


def test_varbinds_are_carried_verbatim() -> None:
    vb = Varbind(oid="1.3.6.1.2.1.2.2.1.1.7", kind="Integer", value="7")
    e = TrapEvent(device="10.0.0.1", trap_oid="1.3.6.1.4.1.9.1", ts=1.0, varbinds=[vb])
    assert e.varbinds[0].value == "7"


def test_missing_required_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        TrapEvent(device="10.0.0.1", ts=1.0)  # type: ignore[call-arg]
