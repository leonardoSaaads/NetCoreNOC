"""The v0.30.0 screens in the DOM harness: sign-in and recovery, the fixed admin role, Settings →
SNMP and Email, the API reference and the visibility examples (ADRs #443-#446).

Driven against route tables captured from the real server (`uifixtures`) where a session exists,
and against a hand-made one-route table for the signed-out card, whose only read is a boolean.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store import Store

import domdriver
import uifixtures
from domdriver import dom_test

TOKEN = "A" * 43


def _signed_out(**routes: Any) -> dict[str, Any]:
    table = {path: {"status": 200, "json": body} for path, body in routes.items()}
    return domdriver.run_scenario("render", {"routes": table})


async def _admin(store: Store, where: str) -> dict[str, Any]:
    routes = await uifixtures.all_routes(store)
    return domdriver.run_scenario("render", {"routes": routes["admin"], "navigate": where})


@dom_test
def test_the_sign_in_card_offers_recovery_only_when_the_appliance_can_send() -> None:
    offered = _signed_out(**{"/api/login/options": {"recovery": True}})
    assert offered["loginVisible"] and "Forgot your password?" in offered["dump"]
    assert "#forgotBtn" in offered["dump"]
    plain = _signed_out()
    assert "#forgotBtn" not in plain["dump"] and "An admin can reset it" in plain["dump"]
    # The two declarations that made the card taller than a laptop are gone from it (ADR #446).
    assert "Two-factor" not in plain["dump"] and "Cannot get in" not in plain["dump"]


@dom_test
def test_a_reset_link_opens_the_new_password_form() -> None:
    result = domdriver.run_scenario("render", {"routes": {}, "hash": f"#/reset/{TOKEN}"})
    assert result["loginVisible"]
    assert "Choose a new password" in result["dump"]
    assert "#rp1" in result["dump"] and "#rp2" in result["dump"]


@dom_test
async def test_the_admin_role_is_drawn_as_a_fact_not_a_control(store: Store) -> None:
    routes = await uifixtures.all_routes(store)
    params = {"routes": routes["admin"], "navigate": "#/access?tab=roles", "click": ["Admin"]}
    result = domdriver.run_scenario("submitForm", params)
    assert "always holds every capability" in result["dump"]
    assert "Save admin" not in result["dump"]


@dom_test
async def test_settings_has_an_snmp_tab_with_versions_communities_and_users(store: Store) -> None:
    dump = (await _admin(store, "#/settings?tab=snmp"))["dump"]
    assert ".snmp-versions" in dump
    for text in ("SNMPv1", "SNMPv2c", "SNMPv3", "Accept any community", "Add a user"):
        assert text in dump, text
    assert "Replay protection" in dump


@dom_test
async def test_settings_has_an_email_tab_with_providers_and_a_test(store: Store) -> None:
    dump = (await _admin(store, "#/settings?tab=email"))["dump"]
    for text in ("Gmail / Google Workspace", "Microsoft 365 / Outlook.com", "Custom SMTP server"):
        assert text in dump, text
    assert "Send a test message to" in dump and "Console address for links" in dump


@dom_test
async def test_the_token_screen_teaches_the_api(store: Store) -> None:
    result = await _admin(store, "#/access?tab=tokens")
    assert "GET /api/reference" in result["requestPaths"]
    dump = result["dump"]
    assert "Use the API" in dump and ".apiref" in dump
    assert "List situations (filter by status, element)" in dump
    assert "every route" in dump  # the filter by role or token


@dom_test
async def test_the_visibility_tab_shows_worked_examples(store: Store) -> None:
    dump = (await _admin(store, "#/access?tab=visibility"))["dump"]
    assert "A regional team sees its region" in dump and "Use this" in dump
    assert "10.20.0.0/16" in dump
