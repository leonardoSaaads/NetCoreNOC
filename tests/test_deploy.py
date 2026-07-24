"""Deployment-hardening assertions for the v0.5.0 security review (F15, F16, F18).

Text-based and stdlib-only (no YAML dependency): the committed deployment artifacts must express
the hardened run, the sdist/image must not carry tests or secrets, and no secret may sit in a
compose/image layer. These back the SECURITY-REVIEW-0.5 findings with passing regression tests.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# --- F15: docker-compose hardening + no secret in the compose file --------------------------


def test_compose_reproduces_the_hardened_run() -> None:
    compose = _read("docker-compose.yml")
    for directive in (
        "read_only: true",
        "cap_drop:",
        "- ALL",
        "no-new-privileges:true",
        "tmpfs:",
        "/tmp",
        "netcorenoc-data:/home/netcorenoc",  # named volume for the DB
        "restart: unless-stopped",
        "healthcheck:",
        "/healthz",
        "162:162/udp",
        "8080:8080",
    ):
        assert directive in compose, (
            f"docker-compose.yml missing hardening directive: {directive!r}"
        )


def test_compose_binds_trap_port_with_single_added_capability() -> None:
    # Look only at active (non-comment) lines so explanatory comments don't confuse the check.
    active = "\n".join(
        line
        for line in _read("docker-compose.yml").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "cap_drop:" in active and "- ALL" in active
    assert (
        "cap_add:" in active and "- CAP_NET_BIND_SERVICE" in active
    )  # the one deliberate add-back
    # drop-all-then-add-one posture: the drop precedes the add.
    assert active.index("cap_drop:") < active.index("cap_add:")


def test_no_secret_material_in_compose_or_env_example() -> None:
    for rel in ("docker-compose.yml", ".env.example"):
        text = _read(rel)
        assert "BEGIN " not in text and "PRIVATE KEY" not in text, f"key material in {rel}"
        # env values are references/blank/commented — never an inline password/token literal.
        for bad in ("PASSWORD=", "SECRET="):
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                assert bad not in line, f"inline secret in {rel}: {line!r}"


def test_env_is_git_ignored_and_only_example_is_committed() -> None:
    gitignore = _read(".gitignore")
    assert any(line.strip() == ".env" for line in gitignore.splitlines()), ".env must be ignored"
    assert (REPO_ROOT / ".env.example").is_file(), ".env.example must be committed"
    assert not (REPO_ROOT / ".env").exists(), "a real .env must never be committed"


# --- F16: systemd unit hardening ------------------------------------------------------------


def test_systemd_unit_is_hardened() -> None:
    unit = _read("deploy/netcorenoc.service")
    for directive in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        "ReadWritePaths=",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
        "MemoryDenyWriteExecute=true",
        "SystemCallFilter=@system-service",
        "RestrictNamespaces=true",
        "LockPersonality=true",
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE",
        "AmbientCapabilities=CAP_NET_BIND_SERVICE",
    ):
        assert directive in unit, f"systemd unit missing hardening directive: {directive!r}"


# --- F18: packaging integrity (sdist/image carry no tests or secrets) -----------------------


def test_manifest_prunes_dev_trees_from_sdist() -> None:
    manifest = _read("MANIFEST.in")
    for pruned in ("prune tests", "prune eval", "prune tools", "prune docs"):
        assert pruned in manifest, f"MANIFEST.in must {pruned!r} (keep tests out of the sdist)"
    assert "graft src" in manifest, "MANIFEST.in must ship the whole src/ package"
    assert "global-exclude .env" in manifest and "*.db" in manifest


def test_dockerignore_excludes_secrets_and_dev_trees() -> None:
    di = _read(".dockerignore")
    for excluded in (".git", ".env", "*.db", "tests/", ".venv"):
        assert excluded in di, f".dockerignore must exclude {excluded!r} from the build context"


def test_notice_and_third_party_license_present() -> None:
    assert (REPO_ROOT / "NOTICE").is_file(), "Apache-2.0 NOTICE required"
    assert "d3" in _read("NOTICE")
    assert (REPO_ROOT / "src/netcorenoc/ui/vendor/d3.LICENSE").is_file()
