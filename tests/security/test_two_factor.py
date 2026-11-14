"""Two-factor sign-in (ADR #482): the codes, the QR code, the two steps, recovery and resets.

Most of the suite runs with the authenticator's clock frozen and codes reusable
(`conftest._frozen_authenticator`); the tests here that are about the clock and the replay guard
are marked ``real_authenticator`` and get production's behaviour.
"""

from __future__ import annotations

import hashlib
import time

import pytest

from netcorenoc.crosscutting import auth, qrcode, totp, twostep
from netcorenoc.store import Store

import authutil

#: RFC 6238 Appendix B's SHA-1 key, "12345678901234567890", in base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
OTHER_SECRET = "ONSWG4TFOQZGKZDTMVSWYZLDN5SGK3TU"


# -- the codes ------------------------------------------------------------------------------------


@pytest.mark.real_authenticator
@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_codes_match_rfc_6238_appendix_b(at: int, expected: str) -> None:
    """The six low digits of the RFC's eight-digit SHA-1 vectors."""
    assert totp.code_at(RFC_SECRET, totp.step_at(at)) == expected


@pytest.mark.real_authenticator
def test_a_code_is_accepted_one_step_either_side_and_only_once() -> None:
    now = 1_700_000_000.0
    step = totp.step_at(now)
    for offset in (-1, 0, 1):
        code = totp.code_at(RFC_SECRET, step + offset)
        assert totp.matching_step(RFC_SECRET, code, now) == step + offset
    assert totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, step + 2), now) is None
    assert totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), now) is None
    code = totp.code_at(RFC_SECRET, step)
    assert totp.matching_step(RFC_SECRET, code, now, last_step=step) is None, "replayed"
    assert totp.matching_step(RFC_SECRET, f" {code[:3]} {code[3:]} ", now) == step, "spaces"
    assert totp.matching_step(RFC_SECRET, "12345", now) is None
    assert totp.matching_step(RFC_SECRET, "abcdef", now) is None


def test_production_refuses_a_code_twice() -> None:
    """The suite turns the guard off for speed; the shipped module has it on (like scrypt's N)."""
    import importlib

    fresh = importlib.reload(totp)
    try:
        assert fresh.REPLAY_GUARD is True
        assert fresh.WINDOW == 1 and fresh.PERIOD_S == 30 and fresh.DIGITS == 6
    finally:
        importlib.reload(totp)


def test_secrets_and_recovery_codes_are_fresh_and_well_formed() -> None:
    secrets = {totp.new_secret() for _ in range(50)}
    assert len(secrets) == 50 and all(len(s) == 32 for s in secrets)
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 == len(set(codes))
    assert all(totp.is_recovery_shape(c) for c in codes)
    assert totp.normalise_recovery(" " + codes[0].upper().replace("-", "") + " ") == codes[0]
    assert not totp.is_recovery_shape("123456")
    assert totp.recovery_digest(codes[0]) == totp.recovery_digest(codes[0].upper())
    assert codes[0] not in totp.recovery_digest(codes[0])


def test_the_provisioning_link_names_the_issuer_and_the_account() -> None:
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "noc admin")
    assert uri == "otpauth://totp/NetCoreNOC:noc%20admin?secret=JBSWY3DPEHPK3PXP&issuer=NetCoreNOC"


# -- the QR code ----------------------------------------------------------------------------------


def test_the_qr_code_matches_the_reference_encoder() -> None:
    """The matrix for a typical enrolment link, as Project Nayuki's reference encoder draws it
    (version 6, level M, mask 1). The digest was computed with that encoder; this module is a
    separate implementation, so an equal digest is two implementations agreeing."""
    text = (
        "otpauth://totp/NetCoreNOC:admin?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=NetCoreNOC"
    )
    rows = ["".join("1" if d else "0" for d in row) for row in qrcode.encode(text)]
    assert len(rows) == 41 == len(rows[0])
    digest = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    assert digest == "6388f5416f755bef4aefcb9eba1cb8f2b0eb724be624539acc3a13caa6b92756"


def test_the_qr_code_grows_with_its_text_and_refuses_what_does_not_fit() -> None:
    assert len(qrcode.encode("a")) == 21
    assert len(qrcode.encode("a" * 120)) > 41
    finder = [row[:7] for row in qrcode.encode("NetCoreNOC")[:7]]
    assert finder[0] == [True] * 7 and finder[1] == [True] + [False] * 5 + [True]
    with pytest.raises(qrcode.QrError):
        qrcode.encode("x" * 2000)


# -- signing in -----------------------------------------------------------------------------------


async def _challenge(client: object, username: str = "edt") -> dict[str, object]:
    first = await client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": username, "password": authutil.PW}
    )
    assert first.status_code == 200, first.text
    assert "set-cookie" not in first.headers, "a right password alone opens no session"
    body: dict[str, object] = first.json()
    return body


async def test_an_enrolled_person_needs_the_code_after_the_password(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        step = await _challenge(client)
        assert step["second_factor"] == "verify" and "enrollment" not in step
        assert (await client.get("/api/me")).status_code == 401
        wrong = await client.post(
            "/api/login/code",
            json={"challenge": step["challenge"], "code": authutil.code(OTHER_SECRET)},
        )
        assert wrong.status_code == 401 and "4 more tries" in wrong.json()["detail"]
        right = await client.post(
            "/api/login/code", json={"challenge": step["challenge"], "code": authutil.OTP}
        )
        assert right.status_code == 200 and right.json()["second_factor"] == "totp"
        me = (await client.get("/api/me")).json()
        assert me["two_factor"]["enrolled"] is True
        assert "secret" not in str(me["two_factor"]).lower()
        again = await client.post(
            "/api/login/code", json={"challenge": step["challenge"], "code": authutil.OTP}
        )
        assert again.status_code == 410, "a challenge is used once"


async def test_five_wrong_codes_end_the_challenge(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        step = await _challenge(client)
        bad = {"challenge": step["challenge"], "code": authutil.code(OTHER_SECRET)}
        answers = [(await client.post("/api/login/code", json=bad)).status_code for _ in range(5)]
        assert answers == [401, 401, 401, 401, 410]
        good = {"challenge": step["challenge"], "code": authutil.OTP}
        assert (await client.post("/api/login/code", json=good)).status_code == 410


async def test_a_right_password_does_not_reset_the_code_throttle(store: Store) -> None:
    """Knowing the password buys no extra guesses at the code: after enough wrong codes the
    person is locked out of the second step even though each new challenge began right."""
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        statuses = []
        for _ in range(2):
            step = await _challenge(client)
            bad = {"challenge": step["challenge"], "code": authutil.code(OTHER_SECRET)}
            for _ in range(4):
                statuses.append((await client.post("/api/login/code", json=bad)).status_code)
        assert 429 in statuses, statuses
        step = await _challenge(client)
        good = {"challenge": step["challenge"], "code": authutil.OTP}
        assert (await client.post("/api/login/code", json=good)).status_code == 429


async def test_a_recovery_code_signs_in_once(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    codes = totp.new_recovery_codes()
    async with store.lock:
        user = await store.get_user_by_name("edt")
        assert user is not None
        await store.replace_recovery_codes(
            int(user["id"]), [totp.recovery_digest(c) for c in codes], 0.0
        )
        await store.commit()
    async with authutil.new_client(app) as client:
        step = await _challenge(client)
        used = await client.post(
            "/api/login/code",
            json={"challenge": step["challenge"], "code": codes[0].upper()},
        )
        assert used.status_code == 200
        assert used.json()["second_factor"] == "recovery" and used.json()["recovery_left"] == 9
        step = await _challenge(client)
        reused = await client.post(
            "/api/login/code", json={"challenge": step["challenge"], "code": codes[0]}
        )
        assert reused.status_code == 401, "a recovery code works once"


async def test_enrolment_happens_only_when_the_first_code_is_right(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        await store.create_user("new", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        await store.commit()
    async with authutil.new_client(app) as client:
        abandoned = await _challenge(client, "new")
        assert abandoned["second_factor"] == "enroll"
        user = await store.get_user_by_name("new")
        assert user is not None and await store.second_factor(int(user["id"])) is None
        step = await _challenge(client, "new")
        enrolment = step["enrollment"]
        assert isinstance(enrolment, dict)
        assert enrolment["secret"] != abandoned["enrollment"]["secret"]  # type: ignore[index]
        assert all(set(row) <= {"0", "1"} for row in enrolment["qr"])
        secret = str(enrolment["secret"]).replace(" ", "")
        old = await client.post(
            "/api/login/code",
            json={"challenge": abandoned["challenge"], "code": authutil.code(secret)},
        )
        assert old.status_code == 410, "only the newest challenge stands"
        done = await client.post(
            "/api/login/code",
            json={"challenge": step["challenge"], "code": authutil.code(secret)},
        )
        assert done.status_code == 200 and done.json()["second_factor"] == "enrolled"
        factor = await store.second_factor(int(user["id"]))
        assert factor is not None and factor["recovery_left"] == 10
        cur = await store.conn.execute(
            "SELECT secret FROM user_second_factor WHERE user_id=?", (int(user["id"]),)
        )
        row = await cur.fetchone()
        assert row is not None and str(row[0]).startswith("nc1:") and secret not in str(row[0])
        recovery = enrolment["recovery_codes"][0]
        step = await _challenge(client, "new")
        assert step["second_factor"] == "verify"
        assert (
            await client.post(
                "/api/login/code", json={"challenge": step["challenge"], "code": recovery}
            )
        ).status_code == 200, "the codes shown at enrolment are the ones that work"


async def test_a_challenge_expires(store: Store) -> None:
    _engine, _queue, _app = await authutil.make_env(store)
    user = await store.get_user_by_name("edt")
    assert user is not None
    now = time.time()
    async with store.lock:
        begun = await twostep.begin(store, int(user["id"]), "edt", "10.0.0.1", now)
        await store.commit()
        late = await twostep.finish(
            store,
            auth.LoginThrottle(),
            challenge=begun.challenge,
            code=authutil.OTP,
            source_ip="10.0.0.1",
            now=now + twostep.CHALLENGE_TTL_S + 1,
        )
        await store.rollback()
    assert late.status == "expired"


@pytest.mark.real_authenticator
async def test_a_code_used_to_sign_in_is_refused_the_second_time(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        code = authutil.code()
        step = await _challenge(client)
        first = await client.post(
            "/api/login/code", json={"challenge": step["challenge"], "code": code}
        )
        assert first.status_code == 200
        step = await _challenge(client)
        second = await client.post(
            "/api/login/code", json={"challenge": step["challenge"], "code": code}
        )
        assert second.status_code == 401, "the same code, replayed"


# -- the person's own authenticator ---------------------------------------------------------------


async def test_new_recovery_codes_need_the_password_and_a_code(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "editor")
    try:
        url = "/api/me/recovery-codes"
        no_code = await client.post(url, json={"password": authutil.PW, "code": "000000"})
        assert no_code.status_code == 403
        no_pw = await client.post(url, json={"password": "wrong-password-1", "code": authutil.OTP})
        assert no_pw.status_code == 403
        made = await client.post(url, json={"password": authutil.PW, "code": authutil.OTP})
        assert made.status_code == 200 and len(made.json()["recovery_codes"]) == 10
        assert (await client.get("/api/me")).json()["two_factor"]["recovery_left"] == 10
    finally:
        await client.aclose()


async def test_forgetting_my_authenticator_signs_me_out_and_enrols_again(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "editor")
    try:
        gone = await client.post(
            "/api/me/two-factor/reset", json={"password": authutil.PW, "code": authutil.OTP}
        )
        assert gone.status_code == 200
        assert (await client.get("/api/me")).status_code == 401, "every session ended"
        step = await _challenge(client)
        assert step["second_factor"] == "enroll"
    finally:
        await client.aclose()


async def test_an_admin_resets_someone_elses_authenticator_never_their_own(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        edt = await store.get_user_by_name("edt")
        adm = await store.get_user_by_name("adm")
        assert edt is not None and adm is not None
        own = await admin.post(f"/api/users/{adm['id']}/two-factor/reset")
        assert own.status_code == 400
        assert (await editor.post(f"/api/users/{adm['id']}/two-factor/reset")).status_code == 403
        done = await admin.post(f"/api/users/{edt['id']}/two-factor/reset")
        assert done.status_code == 200 and done.json()["reset"] is True
        assert (await editor.get("/api/me")).status_code == 401, "their sessions ended"
        assert await store.second_factor(int(edt["id"])) is None
        assert (await admin.post("/api/users/999999/two-factor/reset")).status_code == 404
    finally:
        await admin.aclose()
        await editor.aclose()
