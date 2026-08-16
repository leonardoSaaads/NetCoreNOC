"""Apply each injected defect, measure RED, revert, measure GREEN, with a control in both.

Every injection is a literal string substitution against the corrected tree, applied and reverted
in-process. The tree's hashes are verified after the last one, so a demonstration that left a
defect behind is visible rather than assumed away.
"""

from __future__ import annotations

import hashlib
import json
import shutil

# B404 is suppressed on the import below: subprocess is how this tool runs pytest and git. Every
# call site passes a fixed argv list with `shell=False`, and both executables are absolute paths
# resolved here rather than looked up by the shell at call time.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any, TypedDict

#: Derived, not hardcoded. The first version of this file pinned the build container's absolute
#: path, so the command `docs/gates/v0.13.0-phase-7.md` tells a reader to run failed on every
#: machine except the one it was written on — a reproduction instruction that cannot reproduce.
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".demos"

#: The interpreter running this script, so the demonstrations use the same environment the operator
#: invoked. Pinning `.venv/bin/python` assumed a virtualenv at a fixed path; CI installs into the
#: system Python and would have run a `python` that does not exist.
PY = sys.executable

#: Resolved ONCE, to an absolute path. This is bandit's B607 and it is a real property, not a
#: formality: a partial name is resolved against `PATH` at call time, so a directory earlier on
#: `PATH` decides which `git` runs. Resolving here also makes the absence of git an explicit error
#: instead of a confusing failure inside a subprocess.
GIT = shutil.which("git")


def git(*args: str) -> str:
    """Run git by absolute path and return its stdout.

    `check=True`: every use below is a step the demonstration depends on, and a silent failure
    would leave a tracked injection file behind — the one outcome this tool exists to rule out.
    """
    if GIT is None:
        raise RuntimeError(
            "git was not found on PATH. The demonstrations stage and unstage one created file, "
            "and verify the tree afterwards, so git is required rather than optional."
        )
    # B603: fixed argv, `shell=False`, and no element comes from operator input — `GIT` is an
    # absolute path and every argument is a literal from the DEMOS table in this file.
    proc = subprocess.run(  # nosec B603
        [GIT, *args], cwd=str(ROOT), capture_output=True, text=True, check=True
    )
    return proc.stdout


#: The control must pass in BOTH states, so a red that came from breaking the suite generally is
#: distinguishable from a red the guard actually caught.
CONTROL = "tests/test_ui_invariants.py::test_a_confirm_never_carries_exclusions"


def run(test_ids: list[str], timeout: int = 400) -> str:
    # B603: `shell=False` with a fixed argv — `PY` is `sys.executable`, the flags are literals,
    # and `test_ids` are node ids from the DEMOS table in this file. Nothing here is operator
    # input, and no shell parses any of it.
    proc = subprocess.run(  # nosec B603
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *test_ids],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    lines = [
        line
        for line in proc.stdout.splitlines()
        if line.startswith("FAILED")
        or line.startswith("ERROR")
        or (" passed" in line or " failed" in line or " error" in line)
    ]
    return "\n".join(lines[-6:]) or proc.stdout.strip()[-400:]


class Injection(TypedDict, total=False):
    """One injected defect. `file`+`old`+`new` edits a file; `create`+`content` adds one.

    A `TypedDict` rather than a bare dict because `mypy --strict` covers `tools/` and the loop below
    indexes these by key — an untyped table would make every access `object` and the type checker
    would stop having an opinion about the one script whose correctness decides whether the
    demonstrations mean anything.
    """

    n: int
    title: str
    file: str
    old: str
    new: str
    create: str
    content: str
    track: bool
    guards: list[str]


DEMOS: list[Injection] = [
    {
        "n": 1,
        "title": "A capability lowered so a viewer would see an admin screen",
        "file": "src/netcorenoc/ui/app/registry.js",
        "old": '    capability: "audit.read", component: Audit,',
        "new": '    capability: "situations.read", component: Audit,',
        "guards": [
            "tests/test_ui_invariants.py::test_a_role_never_renders_a_view_whose_capability_it_lacks",
            "tests/test_ui_invariants.py::test_no_admin_capability_produces_a_view_for_a_non_admin",
            "tests/test_security_ui.py::test_admin_views_are_gated_on_an_admin_ceiling_capability",
        ],
    },
    {
        "n": 2,
        "title": "The escaping discipline bypassed on one render path",
        "file": "src/netcorenoc/ui/app/views/entities.js",
        "old": '        <span class="sid">${ne.label || ne.ip}</span>',
        "new": '        <span class="sid" dangerouslySetInnerHTML=${{ __html: ne.label || ne.ip }}></span>',
        "guards": [
            "tests/test_ui_invariants.py::test_no_render_path_turns_operator_supplied_text_into_markup",
            "tests/test_security_ui.py::test_ui_source_has_no_f1_antipatterns",
        ],
    },
    {
        "n": 3,
        "title": "The partial-split gesture sending one id the operator never marked",
        "file": "src/netcorenoc/ui/app/views/situations.js",
        "old": "      body.excluded_ids = detail.alarms\n        .map((a) => a.id).filter((id) => this.state.marked.has(id));",
        "new": "      body.excluded_ids = detail.alarms\n        .map((a) => a.id).filter((id, i) => this.state.marked.has(id) || i === 0);",
        "guards": [
            "tests/test_ui_invariants.py::test_a_partial_split_sends_exactly_the_marked_ids_and_no_others",
        ],
    },
    {
        "n": 4,
        "title": "An SSE update destroying the gesture in flight (the v0.7.5 defect)",
        "file": "src/netcorenoc/ui/app/store.js",
        "old": "  if (update.situations) state.situations = update.situations;\n  for (const sid of state.held.keys()) {",
        "new": "  if (update.situations) state.situations = update.situations;\n  state.held.clear();\n  for (const sid of state.held.keys()) {",
        "guards": [
            "tests/test_ui_invariants.py::test_an_sse_update_mid_gesture_does_not_destroy_the_click_target",
            "tests/test_ui_invariants.py::test_a_held_card_says_it_is_stale_while_it_is_held",
        ],
    },
    {
        "n": 5,
        "title": "F53's repair reverted: a view fetches before its capability is resolved",
        "file": "src/netcorenoc/ui/app/shell.js",
        "old": '    if (decision.kind === "refused") {\n      return html`<${Refused} view=${decision.view} missing=${decision.missing} />`;\n    }',
        "new": '    if (decision.kind === "refused") {\n      const Leaked = decision.view.component;\n      return html`<div class="view" data-view=${decision.view.id}><${Leaked} params=${[]} /></div>`;\n    }',
        "guards": [
            "tests/test_ui_invariants.py::test_a_view_reached_by_address_without_its_capability_refuses_by_decision",
            "tests/test_ui_invariants.py::test_a_capability_the_client_lacks_produces_no_request_at_all",
            "tests/test_security_ui.py::test_capability_is_resolved_before_anything_is_constructed",
        ],
    },
    {
        "n": 6,
        "title": "A hardening-only value accepted below the project floor",
        "file": "src/netcorenoc/ui/app/parameters.js",
        "old": "  const loosens = direction === MIN ? value < floor : value > floor;\n  if (!loosens) return null;",
        "new": "  const loosens = false;\n  if (!loosens) return null;",
        "guards": [
            "tests/test_ui_invariants.py::test_a_hardening_only_value_cannot_be_lowered_through_this_surface",
        ],
    },
    {
        "n": 7,
        "title": "A destructive control applying without a preview",
        "file": "src/netcorenoc/ui/app/destructive.js",
        "old": '      <p class="consequence" role="note"><b>This cannot be undone.</b> ${consequence}</p>',
        "new": '      <p class="quiet-note">${consequence}</p>',
        "guards": [
            "tests/test_ui_invariants.py::test_every_destructive_control_states_its_consequence_before_it_can_be_used",
            "tests/test_security_ui.py::test_every_destructive_control_goes_through_one_component",
        ],
    },
    {
        "n": 8,
        "title": "The sidebar made unreachable by keyboard (sixteen tab stops)",
        "file": "src/netcorenoc/ui/app/sidebar.js",
        "old": '              tabindex=${index === focusIndex ? "0" : "-1"}',
        "new": '              tabindex="0"',
        "guards": [
            "tests/test_ui_invariants.py::test_the_sidebar_is_one_tab_stop_and_is_operable_by_keyboard",
        ],
    },
    {
        "n": 9,
        "title": "A hostile theme cookie reaching the document",
        "file": "src/netcorenoc/ui/app/theme.js",
        "old": "function validated(value, allowed, fallback) {\n  return allowed.includes(value) ? value : fallback;\n}",
        "new": "function validated(value, allowed, fallback) {\n  return value == null ? fallback : value;\n}",
        "guards": [
            "tests/test_ui_invariants.py::test_the_theme_persists_without_localstorage_and_a_hostile_cookie_cannot_widen_it",
        ],
    },
    {
        "n": 10,
        "title": "The vendored framework's checksum altered",
        "file": "src/netcorenoc/ui/vendor/CHECKSUMS.txt",
        "old": "c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8  preact-10.29.8.module.js",
        "new": "0000000000000000000000000000000000000000000000000000000000000000  preact-10.29.8.module.js",
        "guards": ["tests/test_supply_chain.py::test_vendored_assets_match_pinned_checksums"],
    },
    {
        "n": 11,
        "title": "A vendored asset dropped in with no pin at all",
        "create": "src/netcorenoc/ui/vendor/sneaky-1.0.0.module.js",
        "content": "export const x = 1;\n",
        "guards": ["tests/test_supply_chain.py::test_every_vendored_asset_is_pinned_by_name"],
    },
    {
        "n": 12,
        "title": "The CSP relaxed to 'unsafe-inline'",
        "file": "src/netcorenoc/api/perimeter.py",
        "old": "\"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; \"",
        "new": "\"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self'; \"",
        "guards": [
            "tests/test_security_ui.py::test_csp_is_unchanged_and_forbids_inline",
            "tests/test_security_ui.py::test_security_headers_on_every_route_class",
        ],
    },
    {
        "n": 13,
        "title": "A package.json added to the tracked tree",
        "create": "package.json",
        "content": '{"name": "netcorenoc-ui", "dependencies": {"preact": "^10"}}\n',
        "track": True,
        "guards": [
            "tests/test_build_step.py::test_the_tracked_tree_contains_no_build_step_apparatus"
        ],
    },
    {
        "n": 14,
        "title": "A module served but missing from the enumerated allowlist",
        "file": "src/netcorenoc/api/routes_static.py",
        "old": '    "app/views/promotion.js",\n',
        "new": "",
        "guards": [
            "tests/test_supply_chain.py::test_the_served_module_set_equals_the_module_set_on_disk",
            "tests/test_security_ui.py::test_the_ui_tree_is_exactly_what_is_declared",
        ],
    },
    {
        "n": 15,
        "title": "A JavaScript module grown past the 400-line guard",
        "file": "src/netcorenoc/ui/app/format.js",
        "old": "export function alarmName(alarm) {",
        "new": "\n".join(f"// filler {i}" for i in range(320))
        + "\nexport function alarmName(alarm) {",
        "guards": ["tests/test_architecture.py::test_no_javascript_module_is_over_the_size_guard"],
    },
]


def main() -> None:
    # Created here rather than at import. A module that writes to the repository merely by being
    # imported means `mypy`, `bandit` and `ruff` all leave a directory behind, and this tool's
    # closing check is precisely "is the tree as it was".
    OUT.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    control_before = run([CONTROL])
    print(f"control, corrected tree: {control_before}\n", flush=True)

    for demo in DEMOS:
        n, title = demo["n"], demo["title"]
        guards: list[str] = demo["guards"]
        print(f"--- {n}. {title}", flush=True)
        created: Path | None = None
        target: Path | None = None
        original = ""

        if demo.get("create"):
            created = ROOT / demo["create"]
            created.parent.mkdir(parents=True, exist_ok=True)
            created.write_text(demo["content"], encoding="utf-8")
            if demo.get("track"):
                git("add", "-f", demo["create"])
        else:
            target = ROOT / demo["file"]
            original = target.read_text(encoding="utf-8")
            assert demo["old"] in original, f"injection {n}: anchor not found in {demo['file']}"
            target.write_text(original.replace(demo["old"], demo["new"], 1), encoding="utf-8")

        red = run(guards)
        control_red = run([CONTROL])

        if created is not None:
            if demo.get("track"):
                git("rm", "-q", "-f", "--cached", demo["create"])
            created.unlink()
        elif target is not None:
            target.write_text(original, encoding="utf-8")

        green = run(guards)
        results.append(
            {
                "n": n,
                "title": title,
                "file": demo.get("file") or demo.get("create"),
                "guards": guards,
                "red": red,
                "green": green,
                "control_under_injection": control_red,
                "caught": "failed" in red or "error" in red.lower(),
                "restored": "failed" not in green and "error" not in green.lower(),
                "control_held": "1 passed" in control_red,
            }
        )
        print(f"    RED:   {red.splitlines()[-1] if red else '(none)'}")
        print(f"    GREEN: {green.splitlines()[-1] if green else '(none)'}")
        print(
            f"    caught={results[-1]['caught']} restored={results[-1]['restored']} "
            f"control_held={results[-1]['control_held']}\n",
            flush=True,
        )

    (OUT / "demos.json").write_text(json.dumps(results, indent=2))

    # The tree must be exactly as it was. A demonstration that left a defect behind is the worst
    # possible outcome, so this is measured rather than assumed.
    status = git("status", "--porcelain").strip()
    ui = ROOT / "src" / "netcorenoc" / "ui"
    digest = hashlib.sha256(
        b"".join(sorted(p.read_bytes() for p in ui.rglob("*") if p.is_file()))
    ).hexdigest()
    print(f"\ngit status after all injections: {status or '(clean)'}")
    print(f"ui/ tree digest: {digest}")
    missed = [r for r in results if not (r["caught"] and r["restored"] and r["control_held"])]
    print(
        f"\n{len(results) - len(missed)}/{len(results)} demonstrated red, restored green, "
        f"control held in both."
    )
    for r in missed:
        print(f"  NOT DEMONSTRATED: {r['n']}. {r['title']} -> {r}")
    sys.exit(1 if (missed or status) else 0)


main()
