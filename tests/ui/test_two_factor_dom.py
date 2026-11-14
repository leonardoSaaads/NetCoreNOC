"""Signing in, in two steps, as the console draws it (ADR #482)."""

from __future__ import annotations

from netcorenoc.crosscutting import totp, twostep

import authutil
import domdriver
from domdriver import dom_test

CHALLENGE = "c" * 43
ME = {
    "user": "adm",
    "user_id": 2,
    "role": "admin",
    "capabilities": ["stats.read", "self.read"],
    "scope": {"scoped": False, "ne_count": None},
    "password_policy": {"min": 12, "max": 128, "rule": "length"},
    "two_factor": {"enrolled": True, "enrolled_at": 0, "last_used_at": 0, "recovery_left": 10},
}


@dom_test
def test_the_password_card_steps_aside_for_the_qr_code_the_recovery_codes_and_the_code() -> None:
    codes = totp.new_recovery_codes()
    step = twostep.Begun(
        "enroll", CHALLENGE, "adm", secret=authutil.TOTP_SECRET, recovery_codes=codes
    ).response()
    routes = {
        "/api/login/options": {"recovery": False},
        "GET /api/me": {"status": 401, "json": {"detail": "sign in"}},
        "POST /api/login": {"must_change_password": False, **step},
        "POST /api/login/code": {
            "user": "adm",
            "role": "admin",
            "must_change_password": False,
            "second_factor": "enrolled",
        },
    }
    result = domdriver.run_scenario(
        "twoStep",
        {
            "routes": routes,
            "username": "adm",
            "password": authutil.PW,
            "saved": True,
            "code": authutil.OTP,
            "me": ME,
        },
    )
    assert result["panel"] and result["enrol"], "the second step slid in beside the card"
    assert result["cardLocked"], "the password card is locked while the code is asked"
    assert result["qrPaths"] == 1, "the QR code is one inline path, never an image"
    assert result["recovery"] == 0, "the recovery codes are not on the screen until asked for"
    assert not any(c in result["step"] for c in codes) and "codes, hidden." in result["step"]
    assert not any(c in result["masked"] for c in codes), "asked once: their shape, in dots"
    assert "•••••-•••••" in result["masked"]
    assert "Anyone who can see your screen" in result["masked"]
    assert all(c in result["shown"] for c in codes), "asked twice: the codes"
    assert result["codeLockedUntilSaved"], "no code before the recovery codes are kept"
    assert "Scan this QR code" in result["step"] and "I saved my recovery codes" in result["step"]
    assert authutil.TOTP_SECRET not in result["step"], "the key is folded until asked for"
    assert result["posts"] == [
        {"path": "/api/login", "body": {"username": "adm", "password": authutil.PW}},
        {"path": "/api/login/code", "body": {"challenge": CHALLENGE, "code": authutil.OTP}},
    ]
    assert result["signedIn"], "the right code opened the console"
