"""Drive every screen as every role, in one process, and write the gate evidence.

Seventeen views by three roles, then the keyboard walk, the four theme states, the hardening-only
refusal and its control, and the empty and error sweeps — in one harness session so the figures a
gate quotes come from one measurement rather than from several that could disagree.

Writes `.drive/gate.json` and one DOM dump per (role, view). `docs/gates/v0.13.0-phase-7.md` quotes
it; a representative subset is committed under `docs/gates/evidence/v0.13.0/`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

#: Derived from this file's location. Pinning the build container's absolute path made the
#: reproduction command in `docs/gates/v0.13.0-phase-7.md` false everywhere except the machine it
#: was written on — a reproduce-me instruction that cannot reproduce is worse than none.
ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "src", "tools"):
    sys.path.insert(0, str(ROOT / extra))

#: Created in `main()`, not here: importing a module should not write to the repository.
OUT = ROOT / ".drive"

VIEWS = [
    "overview",
    "situations",
    "graph",
    "timeline",
    "entities",
    "classes",
    "labelling",
    "corpus",
    "promotion",
    "users",
    "tokens",
    "settings",
    "scorer",
    "governance",
    "quarantine",
    "audit",
    "account",
]


async def main() -> None:
    import domdriver
    import uifixtures
    from netcorenoc.store import Store

    import authutil
    import util

    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("gate.db*"):
        stale.unlink()
    store = Store(str(OUT / "gate.db"))
    await store.open()
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", uifixtures.BASE))

    admin = await authutil.client_as(app, "admin")
    await admin.post("/api/tokens", json={"name": "gate-token", "role": "viewer"})
    params = (await admin.get("/api/scorer")).json()["params"]
    await admin.post("/api/scorer", json={**params, "note": "gate drive"})
    routes = {}
    for role in ("viewer", "editor", "admin"):
        routes[role] = await uifixtures.capture(app, role)
        for ne in (await admin.get("/api/entities")).json():
            detail = await admin.get(f"/api/entities/{ne['id']}")
            if detail.status_code == 200:
                routes[role][f"/api/entities/{ne['id']}"] = {"status": 200, "json": detail.json()}
        for extra_path in ("/api/classes", "/api/promotion", "/api/dataset/retention"):
            resp = await (await authutil.client_as(app, role)).get(extra_path)
            if resp.status_code == 200:
                routes[role][extra_path] = {"status": 200, "json": resp.json()}
        routes[role]["POST /api/password"] = {"status": 200, "json": {"status": "changed"}}
    await admin.aclose()
    await store.close()

    report: dict[str, Any] = {"perRole": {}}

    # 1. Every view rendered as every role, DOM dumped.
    for role in ("viewer", "editor", "admin"):
        report["perRole"][role] = {}
        for view in VIEWS:
            result = domdriver.run_scenario(
                "render", {"routes": routes[role], "navigate": f"#/{view}"}
            )
            (OUT / f"dump-{role}-{view}.txt").write_text(result["dump"])
            report["perRole"][role][view] = {
                "activeView": result["activeView"],
                "refused": result["refused"],
                "dumpLines": len(result["dump"].splitlines()),
                "requests": result["requestPaths"],
            }
        report["perRole"][role]["_nav"] = domdriver.run_scenario("boot", {"routes": routes[role]})[
            "navHrefs"
        ]

    # 2. Keyboard.
    report["keyboard"] = domdriver.run_scenario(
        "keyboard",
        {"routes": routes["admin"], "keys": ["ArrowDown", "ArrowDown", "End", "Home", "Enter"]},
    )

    # 3. Themes.
    report["themeDefault"] = domdriver.run_scenario("theme", {"routes": routes["admin"]})
    report["themeToggled"] = domdriver.run_scenario(
        "theme", {"routes": routes["admin"], "click": ["Theme:"]}
    )
    report["themeFromCookie"] = domdriver.run_scenario(
        "theme", {"routes": routes["admin"], "cookies": {"ncn_theme": "light"}}
    )
    report["themeHostileCookie"] = domdriver.run_scenario(
        "theme", {"routes": routes["admin"], "cookies": {"ncn_theme": "<script>evil</script>"}}
    )

    # 4. Light theme rendered and dumped.
    light = domdriver.run_scenario(
        "render",
        {"routes": routes["admin"], "cookies": {"ncn_theme": "light"}, "navigate": "#/situations"},
    )
    (OUT / "dump-admin-situations-light.txt").write_text(light["dump"])
    report["light"] = {"theme": light["theme"], "dumpLines": len(light["dump"].splitlines())}

    # 5. The hardening-only refusal, and its control.
    report["hardeningRefused"] = domdriver.run_scenario(
        "submitForm",
        {
            "routes": routes["admin"],
            "navigate": "#/scorer",
            "fields": {"#sc-tau_s": "0.1"},
            "click": ["Preview effect"],
        },
    )
    report["hardeningAccepted"] = domdriver.run_scenario(
        "submitForm",
        {
            "routes": routes["admin"],
            "navigate": "#/scorer",
            "fields": {"#sc-tau_s": "120"},
            "click": ["Preview effect"],
        },
    )

    # 6. Empty states: every view against an empty appliance.
    empty_routes = {
        "/api/me": routes["admin"]["/api/me"],
        "/api/stats": {
            "status": 200,
            "json": {
                "devices": 0,
                "classes": 0,
                "active_alarms": 0,
                "open_situations": 0,
                "latency_p95_s": 0.0,
                "warnings": [],
                "ingest_gaps": [],
                "open_ingest_gaps": [],
            },
        },
        "/api/graph": {"status": 200, "json": {"nodes": [], "edges": []}},
        "/api/situations?limit=50&status=open": {"status": 200, "json": []},
        "/api/classes": {"status": 200, "json": []},
        "/api/entities": {"status": 200, "json": []},
        "/api/state-clears": {"status": 200, "json": []},
        "/api/quarantine?limit=100": {"status": 200, "json": []},
        "/api/audit?limit=200": {"status": 200, "json": []},
        "/api/timeline?limit=300": {"status": 200, "json": {"marks": []}},
        "/api/users": {"status": 200, "json": []},
        "/api/tokens": {"status": 200, "json": []},
        "/api/promotion": {
            "status": 200,
            "json": {
                "promotions": [],
                "model_versions": [],
                "active_model_version_id": None,
                "active_config_id": None,
                "seal_query_count": 0,
                "plan_sha256": "abc",
            },
        },
        "/api/config": routes["admin"].get("/api/config"),
        # Genuinely empty, not admin's populated payload: the first probe reused the real one and
        # then reported the corpus screen as missing an empty state it does in fact have.
        "/api/dataset/retention": {
            "status": 200,
            "json": {
                "sink_days": 7,
                "sink_rows": 100000,
                "training_days": 365,
                "audit_days": 365,
                "capture_enabled": True,
                "stats": {},
                "audit_swept": {},
            },
        },
        "/api/scorer": routes["admin"].get("/api/scorer"),
        "/api/rbac": routes["admin"].get("/api/rbac"),
        "/api/scope": routes["admin"].get("/api/scope"),
    }
    report["empty"] = {}
    for view in VIEWS:
        result = domdriver.run_scenario("render", {"routes": empty_routes, "navigate": f"#/{view}"})
        (OUT / f"empty-{view}.txt").write_text(result["dump"])
        dump = result["dump"]
        report["empty"][view] = {
            "hasEmptyState": ".state-empty" in dump or "state-empty" in dump,
            "hasLoading": "state-loading" in dump,
            "hasError": "state-error" in dump,
            "lines": len(dump.splitlines()),
        }

    # 7. Error states: every route 500s.
    report["errors"] = {}
    for view in VIEWS:
        result = domdriver.run_scenario(
            "render", {"routes": {"/api/me": routes["admin"]["/api/me"]}, "navigate": f"#/{view}"}
        )
        dump = result["dump"]
        report["errors"][view] = {
            "hasError": "state-error" in dump or "state-empty" in dump or "state-partial" in dump,
            "lines": len(dump.splitlines()),
        }
        (OUT / f"error-{view}.txt").write_text(dump)

    (OUT / "gate.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        json.dumps(
            {
                "roles": {
                    r: {
                        "nav": len(report["perRole"][r]["_nav"]),
                        "refused": sorted(
                            v
                            for v, d in report["perRole"][r].items()
                            if v != "_nav" and d["refused"]
                        ),
                    }
                    for r in ("viewer", "editor", "admin")
                },
                "keyboard": {
                    k: report["keyboard"][k]
                    for k in ("tabStops", "itemCount", "navLabel", "headingFocusable")
                },
                "themes": {
                    "default": report["themeDefault"]["after"],
                    "toggled": report["themeToggled"]["after"],
                    "fromCookie": report["themeFromCookie"]["after"],
                    "hostileCookie": report["themeHostileCookie"]["after"],
                },
                "hardening": {
                    "refusedSent": report["hardeningRefused"]["sent"],
                    "refusalShown": report["hardeningRefused"]["refusalShown"],
                    "acceptedSent": [s["path"] for s in report["hardeningAccepted"]["sent"]],
                    "acceptedRefusalShown": report["hardeningAccepted"]["refusalShown"],
                },
                "emptyStatesMissing": [
                    v for v, d in report["empty"].items() if not d["hasEmptyState"]
                ],
                "errorStatesMissing": [v for v, d in report["errors"].items() if not d["hasError"]],
            },
            indent=2,
        )
    )
    print("\nfull evidence: .drive/gate.json and .drive/dump-*.txt")


asyncio.run(main())
