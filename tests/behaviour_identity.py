"""The behaviour-identity harness (v0.15.1, DECISIONS #211).

**What it is for.** v0.15.1 moves 56 files and rewrites every import that names one. In a release
that is entirely moves, *"the tests pass"* is a weaker claim than *"the HTTP surface is unchanged"*,
and the difference is not academic: the assertions were written against the same code that produces
the shape, so a package reorganisation is exactly the change that could alter a response without
failing one of them. v0.7.3 proved 141 store method bodies unchanged by hash and that is what made
its move reviewable. This is the equivalent for the HTTP surface, and it did not exist.

**What it records.** Every route the app registers — all of them, reads, writes and the static
console alike — driven in registration order as each of the four principals the appliance has:
anonymous, viewer, editor and admin. A role is included because *a role that renders differently is
a behaviour*, and three of the four never see most of these responses in the suite.

For each request: the status, every response header except the four in `VOLATILE_HEADERS`, and the
SHA-256 and length of the canonicalised body. A hash rather than the body because the static surface
alone is 47 files and a committed record has to stay readable; a hash is no less byte-sensitive, and
`--bodies` dumps the text when a diff needs explaining.

**Determinism is bought at the source, not by canonicalising the output.** `time.time` is frozen
for the whole run, so every timestamp the engine, the perimeter, the audit chain and the routes
write is the same on every run rather than being erased afterwards. What remains genuinely random
is `secrets.token_urlsafe` — session ids and service-token values — and those are substituted **by
their exact captured value**, never by a pattern. Every substitution the run made is written into
the record's own header, with its reason, so the list is read from the evidence rather than trusted.

**The failure mode this file has to avoid** is an over-broad canonicaliser: one regex over anything
numeric would make the diff pass by deleting the evidence it exists to preserve. Two properties
guard against it — `test_behaviour_identity.py` asserts the record reproduces byte for byte across
two runs *in separate processes* (so an incomplete list shows up as a diff), and it asserts that a
deliberately altered response produces one (so a list that erased everything would fail too).

Run it directly to rewrite the committed record:

    python tests/behaviour_identity.py --write
    python tests/behaviour_identity.py --bodies GET /api/stats
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from netcorenoc.crosscutting import auth
from netcorenoc.store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent

# `util` imports `trap_replay` from `tools/`, which `conftest.py` puts on the path for a pytest
# run. This module is also a command-line program, so it does the same for itself — and therefore
# has to do it before importing the two helpers, which is what the E402s below are.
for _extra in (REPO_ROOT / "tests", REPO_ROOT / "tools"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import authutil  # noqa: E402
import util  # noqa: E402

RECORD = Path(__file__).parent / "fixtures" / "behaviour-identity.txt"

#: The one clock. A round number in the past, chosen so that nothing in the tree treats it as
#: "recent" by accident and so that the value is recognisable in a dumped body.
EPOCH = 1_700_000_000.0

#: The seed. `fiber_cut` is the scenario the eval corpus is anchored on and the one whose shape the
#: console was designed against: two devices, a link failure, and the entity inference that follows.
#: Replayed through `receiver.parse_trap` and the real batch loop, never injected into the store.
SCENARIO = "fiber_cut.json"

#: scrypt work factor, lowered exactly as `conftest._fast_scrypt` lowers it. Set here as well so a
#: run under pytest and a run from the command line produce the same record; the parameters live
#: inside each stored hash, so this changes cost and nothing else, and no response exposes it.
SCRYPT_N = 2**14

ROLES = ("anonymous", "viewer", "editor", "admin")

#: The password `POST /api/password` moves the acting principal to, so a later re-authentication
#: uses the credential that now exists rather than the one the route just replaced.
NEW_PASSWORD = "a different long password"

#: Routes that end or replace the acting principal's session. After one of these the pass
#: re-authenticates (see `_reauthenticate`); they are named rather than detected, because
#: "the response set a cookie" is true of a successful login too and says nothing about whether
#: the session the pass is holding still exists.
SESSION_ENDING = frozenset(
    {("POST", "/api/login"), ("POST", "/api/logout"), ("POST", "/api/password")}
)

#: The one route this harness does not drive, named rather than skipped silently.
#:
#: `GET /api/events` is a server-sent-events stream that, to a principal holding `events.stream`,
#: stays open for the life of the connection and re-authorizes on every snapshot (F30). It has no
#: response to record: `httpx.ASGITransport` reads a response to completion, so the first version
#: of this harness hung on it for as long as it was given. **The suite has never driven it over
#: HTTP either** — `test_governance.py` and `test_declaration.py` call the endpoint function with a
#: constructed ASGI scope, for this reason. Its presence and its position in registration order
#: stay pinned by `test_architecture.ROUTE_ORDER_BASELINE`, and the record below carries a line
#: for it so that "not driven" is visible rather than absent.
NOT_DRIVEN = frozenset({("GET", "/api/events")})

#: Headers dropped from the record, with the reason each one is not a behaviour of this project.
#:
#:   * `date`, `server` — the wall clock and the ASGI server's own name.
#:   * `last-modified`, `etag` — Starlette derives both from the **file's mtime on disk**, so both
#:     change when the repository is cloned, unpacked or checked out and neither says anything
#:     about what the appliance serves. What it serves is pinned exactly, by the body's SHA-256 and
#:     its length, which is the stronger of the two claims and the one this harness is for.
#:
#: Everything else is kept, `set-cookie` included: the flags on a session cookie are a security
#: behaviour, and only the opaque id inside it is substituted.
VOLATILE_HEADERS = ("date", "server", "last-modified", "etag")


class Substitution:
    """One canonicalisation, and the reason it is allowed to exist.

    A substitution is a **literal** — a value captured from this run — mapped to a placeholder.
    Never a pattern: a pattern is what erases the evidence along with the noise.
    """

    def __init__(self, literal: str, placeholder: str, reason: str) -> None:
        self.literal = literal
        self.placeholder = placeholder
        self.reason = reason


# --- driving the surface -------------------------------------------------------------------


class Recorder:
    """One principal's pass over the whole surface, against its own freshly seeded database."""

    def __init__(self, role: str, dump: tuple[str, str] | None = None) -> None:
        self.role = role
        self.dump = dump
        self.substitutions: list[Substitution] = []
        self.lines: list[str] = []
        self.dumped: str | None = None
        self.token_id: str | None = None
        self._session: str | None = None
        self._password = authutil.PW

    def _canonical(self, text: str) -> str:
        for sub in self.substitutions:
            text = text.replace(sub.literal, sub.placeholder)
        return text

    def _record(self, method: str, path: str, response: httpx.Response) -> None:
        headers = sorted(
            (name, self._canonical(value))
            for name, value in response.headers.items()
            if name.lower() not in VOLATILE_HEADERS
        )
        try:
            body = self._canonical(response.content.decode("utf-8"))
            payload = body.encode("utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no route serves non-UTF-8 today
            payload = response.content
            body = ""
        digest = hashlib.sha256(payload).hexdigest()
        rendered = " ".join(f"{name}={value!r}" for name, value in headers)
        self.lines.append(
            f"{self.role:9} {method:6} {path:34} {response.status_code} "
            f"len={len(payload):<7} sha={digest} [{rendered}]"
        )
        if self.dump == (method, path):
            self.dumped = body

    async def run(self, tmp: Path) -> list[str]:
        db = tmp / f"{self.role}.db"
        store = Store(str(db))
        await store.open()
        try:
            engine, queue, app = await authutil.make_env(store)
            await util.drive(engine, queue, util.fixture_events(SCENARIO, EPOCH))
            await self._drive_http(app, store)
        finally:
            await store.close()
        return self.lines

    async def _drive_http(self, app: object, store: Store) -> None:
        client = authutil.new_client(app)
        try:
            await self._reauthenticate(client)
            targets = await _targets(store)
            for method, path in _surface(app):
                await self._request(client, method, path, targets)
        finally:
            await client.aclose()

    async def _reauthenticate(self, client: httpx.AsyncClient) -> None:
        """Restore the pass's own principal after a route that ended its session.

        `/api/logout` sits second in registration order, so without this every one of the 42 routes
        after it is driven anonymously and all four passes collapse into the same record — which is
        exactly what the first run of this harness produced, and what made it worth building.
        Re-authenticating rather than replaying the cookie is the point: the server has revoked
        that session, and a revoked cookie is not the principal.
        """
        client.cookies.clear()
        self._session = None
        if self.role == "anonymous":
            return
        response = await authutil.login(client, self.role, password=self._password)
        assert response.status_code == 200, (self.role, response.status_code, response.text)
        self._session = client.cookies["netcorenoc_session"]
        self._substitute_session(self._session)

    def _substitute_session(self, value: str) -> None:
        if any(sub.literal == value for sub in self.substitutions):
            return
        self.substitutions.append(
            Substitution(
                value,
                f"<session:{self.role}:{len(self.substitutions)}>",
                "an opaque session id from secrets.token_urlsafe; the cookie's presence, its "
                "flags and the id's length are recorded, its value cannot be",
            )
        )

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, targets: dict[str, str]
    ) -> None:
        concrete = path
        for name, value in {**targets, "tid": self.token_id}.items():
            if value is not None:
                concrete = concrete.replace("{" + name + "}", value)
        if "{" in concrete:  # a path parameter nothing in the seed can fill
            return
        body = dynamic_body(path, targets) or REQUEST_BODIES.get((method, path))
        if (method, path) in NOT_DRIVEN:
            self.lines.append(f"{self.role:9} {method:6} {path:34} not-driven (see NOT_DRIVEN)")
            return
        if method == "GET":
            response = await client.get(concrete)
        elif method == "DELETE":
            response = await client.delete(concrete)
        else:
            response = await client.post(concrete, json=body if body is not None else {})
        # Register what this response minted BEFORE recording it, so the record never carries a
        # random value — and restore the principal afterwards. `/api/login` and `/api/logout` are
        # ordinary routes on this walk, and without the restore every route registered after them
        # would be driven as whoever that login named, which would collapse the four passes into
        # one. The walk keeps registration order because FastAPI matches in it.
        self._capture_secrets(method, path, response)
        self._record(method, path, response)
        if (method, path) in SESSION_ENDING:
            if (method, path) == ("POST", "/api/password") and response.status_code == 200:
                self._password = NEW_PASSWORD
            await self._reauthenticate(client)

    def _capture_secrets(self, method: str, path: str, response: httpx.Response) -> None:
        for value in response.headers.get_list("set-cookie"):
            if value.startswith("netcorenoc_session=") and (
                minted := value.split("=", 1)[1].split(";", 1)[0]
            ):
                self._substitute_session(minted)
        # A minted service token is the only other value `secrets` produces that reaches a body.
        if (method, path) == ("POST", "/api/tokens") and response.status_code == 200:
            payload = response.json()
            minted = payload.get("token")
            if isinstance(minted, str) and minted:
                self.substitutions.append(
                    Substitution(
                        minted,
                        f"<token:{self.role}>",
                        "a freshly minted service-token value; the route returns it exactly once "
                        "and it is random by design",
                    )
                )
            # …and the id it was given, so `DELETE /api/tokens/{tid}` names a real token.
            if isinstance(payload.get("id"), int):
                self.token_id = str(payload["id"])


async def _targets(store: Store) -> dict[str, str]:
    """The path parameters, resolved from the seeded database rather than invented.

    A literal id would make most of these routes 404 and the record would then pin the perimeter
    rather than the handler. Each is the lowest id of its kind, so the choice is deterministic.
    """
    async with store.lock:
        situations = await store.list_situations(None, 50)
        nes = await store.list_ne()
        users = await store.list_users()
        members = (
            await store.situation_member_ids(min(int(s["id"]) for s in situations))
            if situations
            else []
        )
    out: dict[str, str] = {}
    if situations:
        out["sid"] = str(min(int(s["id"]) for s in situations))
        # v0.16.0. The second situation, for a `move` and a `merge` — both name **two** of them,
        # which is why they are two routes rather than one (DECISIONS #255). The highest id rather
        # than "the other one", so the value is defined however many the seed forms.
        out["sid2"] = str(max(int(s["id"]) for s in situations))
    if members:
        # `POST /api/alarms/{aid}/clear` names an alarm, not a situation. Resolved from the seed
        # like every other target here: a literal would make the route 404 and the record would
        # then pin the perimeter rather than the handler.
        out["aid"] = str(min(members))
    if nes:
        out["ne_id"] = str(min(int(n["id"]) for n in nes))
    # The viewer account, never the acting principal except on the viewer pass — where every route
    # that names it refuses before the handler. Picking the highest id instead would have the admin
    # pass delete the admin it is authenticated as, midway through its own walk.
    viewer = next((u for u in users if u["username"] == authutil.ROLE_USER["viewer"]), None)
    if viewer is not None:
        out["uid"] = str(int(viewer["id"]))
    return out


def _surface(app: object) -> list[tuple[str, str]]:
    """Every (method, path) the app registers, in registration order.

    Registration order is behaviour — FastAPI matches in it — so the record walks the surface in
    the order the app declares rather than in a sorted order that would hide a reordering.
    """
    out: list[tuple[str, str]] = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, path))
    return out


#: Request bodies for the write routes, declared rather than generated. A route absent from this
#: table is still driven — with `{}` — and what the record then pins is the validation refusal,
#: which is a behaviour too and moves if a response model moves.
_SCORER_PARAMS = {
    "w_t": 0.5,
    "w_a": 0.3,
    "w_e": 0.2,
    "tau_s": 60.0,
    "threshold": 0.5,
    "note": "behaviour-identity harness",
}

REQUEST_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/login"): {"username": authutil.ROLE_USER["viewer"], "password": authutil.PW},
    ("POST", "/api/password"): {"old_password": authutil.PW, "new_password": NEW_PASSWORD},
    ("POST", "/api/users"): {
        "username": "harness",
        "password": "another long password",
        "role": "viewer",
    },
    ("POST", "/api/users/{uid}/role"): {"role": "editor"},
    ("POST", "/api/tokens"): {"name": "svc", "role": "viewer"},
    ("POST", "/api/config"): {"allowlist": "10.0.0.0/8", "retention_days": 30.0},
    ("POST", "/api/dataset/retention"): {
        "sink_days": 30.0,
        "sink_rows": 100_000,
        "training_days": 90.0,
        "audit_days": 365.0,
        "preview": True,
    },
    ("POST", "/api/situations/{sid}/feedback"): {"verdict": "confirm"},
    ("POST", "/api/situations/{sid}/close"): {"verdict": "confirm"},
    ("POST", "/api/labels"): {"kind": "device", "id": 1, "label": "harness"},
    ("POST", "/api/scorer/preview"): dict(_SCORER_PARAMS),
    ("POST", "/api/scorer"): dict(_SCORER_PARAMS),
    ("POST", "/api/scorer/rollback"): {"config_id": 1},
    ("POST", "/api/promotion"): {"model_version_id": 1, "note": "harness"},
    ("POST", "/api/rbac"): {"clear": True, "note": "harness"},
    ("POST", "/api/scope"): {"clear": True, "note": "harness"},
    ("POST", "/api/audit/prune"): {"before": EPOCH - 86400},
}


#: v0.16.0. Bodies that name ids the seed produced, so the three restructuring gestures reach their
#: **handler** rather than stopping at a 422 from the request model. A static dict cannot hold them
#: — the ids are resolved from the seeded database, exactly as the path targets are — so the two
#: sources are composed at request time and this one wins where both have a key.
def dynamic_body(path: str, targets: dict[str, str]) -> dict[str, Any] | None:
    """The body for a route whose payload names a seeded id, or `None`."""
    sid2, aid = targets.get("sid2"), targets.get("aid")
    if path == "/api/situations/{sid}/move" and sid2 and aid:
        return {"alarm_id": int(aid), "to_situation_id": int(sid2), "confidence": 0.8}
    if path == "/api/situations/{sid}/merge" and sid2:
        return {"from_situation_id": int(sid2), "confidence": 0.8}
    if path == "/api/situations/{sid}/split" and aid:
        return {"alarm_ids": [int(aid)], "confidence": 0.8}
    if path == "/api/situations/{sid}/name":
        return {"name": "harness"}
    return None


# --- the frozen clock ----------------------------------------------------------------------


@contextmanager
def _frozen_clock() -> Iterator[None]:
    """`time.time` only. `time.monotonic` stays real, because the engine's drain deadline and the
    rate limiter loop on it and a frozen monotonic would hang or short-circuit them.

    Every call site in `src/` reaches the clock as `time.time()` after `import time`, so patching
    the attribute on the module reaches all of them — verified by `ast`, not assumed.
    """
    real_time, real_n = time.time, auth.SCRYPT_N
    time.time = lambda: EPOCH
    auth.SCRYPT_N = SCRYPT_N
    try:
        yield
    finally:
        time.time = real_time
        auth.SCRYPT_N = real_n


# --- the record ----------------------------------------------------------------------------


def build(tmp: Path, dump: tuple[str, str] | None = None) -> tuple[str, str | None]:
    """The whole record, as text. Returns (record, dumped body if one was asked for)."""

    async def go() -> tuple[list[str], list[Substitution], str | None]:
        lines: list[str] = []
        subs: list[Substitution] = []
        dumped: str | None = None
        for role in ROLES:
            recorder = Recorder(role, dump)
            lines.extend(await recorder.run(tmp))
            subs.extend(recorder.substitutions)
            dumped = dumped or recorder.dumped
        return lines, subs, dumped

    with _frozen_clock():
        lines, subs, dumped = asyncio.run(go())

    header = [
        "# The behaviour-identity record (DECISIONS #211). Regenerate with",
        "#     python tests/behaviour_identity.py --write",
        f"# scenario={SCENARIO} epoch={EPOCH} roles={','.join(ROLES)}",
        f"# dropped headers: {', '.join(VOLATILE_HEADERS)}",
        "# substitutions, every one a literal captured from the run:",
    ]
    header += [f"#   {sub.placeholder} — {sub.reason}" for sub in subs]
    header.append("#")
    return "\n".join([*header, *lines]) + "\n", dumped


def main(argv: list[str]) -> int:
    import tempfile

    dump: tuple[str, str] | None = None
    if "--bodies" in argv:
        at = argv.index("--bodies")
        dump = (argv[at + 1], argv[at + 2])
    with tempfile.TemporaryDirectory() as tmp:
        record, dumped = build(Path(tmp), dump)
    if dumped is not None:
        sys.stdout.write(dumped + "\n")
        return 0
    if "--write" in argv:
        RECORD.write_text(record, encoding="utf-8")
        sys.stdout.write(f"wrote {RECORD.relative_to(REPO_ROOT)}\n")
    else:
        sys.stdout.write(record)
    sys.stderr.write(f"sha256 {hashlib.sha256(record.encode()).hexdigest()}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - the command-line half
    raise SystemExit(main(sys.argv[1:]))
