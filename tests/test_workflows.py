"""GitHub Actions hygiene lint (v0.5.0), stdlib-only and dev-only.

Even though the committed workflows are dormant until the maintainer enables them, they must be
safe by construction: every referenced action is pinned by a full commit SHA (never a floating
tag), every workflow declares a least-privilege ``permissions:`` block, and the opt-in
publishing steps in the release workflow stay commented so nothing can publish by accident.

Parsing is deliberately text-based (comment lines ignored) so the lint needs no YAML dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s]+)@(?P<ref>\S+)")
_SHA = re.compile(r"^[0-9a-f]{40}$")
# Actions that would publish outside the repo — must never appear as an ACTIVE step.
_PUBLISHING = ("pypi-publish", "docker/login-action", "docker/build-push-action", "cosign")


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def _active_uses(path: Path) -> list[tuple[int, str, str]]:
    """(lineno, action, ref) for every non-comment ``uses:`` line."""
    out: list[tuple[int, str, str]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if raw.lstrip().startswith("#"):
            continue  # commented-out example (e.g. the opt-in publish steps)
        m = _USES.match(raw)
        if m:
            out.append((i, m.group("action"), m.group("ref")))
    return out


def test_workflows_exist() -> None:
    assert _workflows(), "no workflows found — this lint would be vacuous"


@pytest.mark.parametrize("wf", _workflows(), ids=lambda p: p.name)
def test_every_action_is_pinned_by_commit_sha(wf: Path) -> None:
    unpinned = [
        f"{wf.name}:{ln} {action}@{ref}"
        for ln, action, ref in _active_uses(wf)
        if not _SHA.match(ref)
    ]
    assert not unpinned, "GitHub Actions must be pinned by 40-char commit SHA:\n  " + "\n  ".join(
        unpinned
    )


@pytest.mark.parametrize("wf", _workflows(), ids=lambda p: p.name)
def test_every_workflow_declares_permissions(wf: Path) -> None:
    active = [
        line
        for line in wf.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert any(re.match(r"^\s*permissions:", line) for line in active), (
        f"{wf.name} must declare a least-privilege permissions: block"
    )


@pytest.mark.parametrize("wf", _workflows(), ids=lambda p: p.name)
def test_no_active_publishing_step(wf: Path) -> None:
    """The opt-in publish/sign steps must stay commented — cannot publish by accident."""
    offenders = [
        f"{wf.name}:{ln} {action}"
        for ln, action, _ in _active_uses(wf)
        if any(marker in action for marker in _PUBLISHING)
    ]
    assert not offenders, "publishing steps must remain commented/opt-in:\n  " + "\n  ".join(
        offenders
    )
