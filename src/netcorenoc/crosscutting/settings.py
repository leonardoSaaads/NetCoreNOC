"""Process configuration: the environment is the whole configuration surface.

`Settings` is read once at startup and never mutated. Everything an operator can set is an
environment variable with a sane default — no config file, no flags, no runtime reconfiguration
beyond the two values `runtime.RuntimeConfig` holds.

The removed-alias errors live here too, beside the reader that would otherwise have ignored them:
a silently ignored setting (an allowlist, a TLS path) is a security regression, not a nuisance.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# Rebrand (v0.4.0): the canonical environment prefix is NETCORENOC_*. The legacy OPTICORR_*
# aliases were accepted with a once-per-variable deprecation warning through v0.5.0 and are
# **removed in v0.6.0** as promised (DECISIONS #34, #39, #45). Setting one is now a hard startup
# error naming the replacement — never a silent no-op: an operator who still set
# OPTICORR_ALLOWLIST would believe traps were filtered while every source was accepted, which is
# a security regression dressed as a compatibility one. This mirrors how v0.3.0 removed
# OPTICORR_API_TOKEN (DECISIONS #29).
ENV_PREFIX = "NETCORENOC_"
LEGACY_ENV_PREFIX = "OPTICORR_"


def read_env(suffix: str, default: str | None = None) -> str | None:
    """Read NETCORENOC_<suffix>. The legacy OPTICORR_<suffix> alias was removed in v0.6.0."""
    return os.environ.get(ENV_PREFIX + suffix, default)


def legacy_env_names(environ: Mapping[str, str] | None = None) -> list[str]:
    """Every removed OPTICORR_* variable present in the environment, sorted. Names only."""
    source = os.environ if environ is None else environ
    return sorted(name for name in source if name.startswith(LEGACY_ENV_PREFIX))


class SettingsError(RuntimeError):
    """A configuration value the process cannot read, named so an operator can fix it (F69).

    The project already refuses two settings by name and says what to do instead. Five more —
    the two ports, the two retention horizons and the allowlist — were converted with a bare
    ``int()`` / ``float()`` / ``ip_network()``, so setting one wrongly produced a twenty-line
    traceback naming the **value** and never the **variable**. Blanking a line in ``.env`` is one
    of the cases, and ``.env.example`` is a file of lines an operator edits.
    """


def _number[T](suffix: str, default: str, convert: Callable[[str], T], expects: str) -> T:
    """Read ``NETCORENOC_<suffix>`` and convert it, or refuse in a sentence that names it."""
    raw = read_env(suffix, default)
    text = default if raw is None else raw
    try:
        return convert(text)
    except ValueError as exc:
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={text!r} is not {expects}. Unset it to use the default "
            f"({default}), or set a value that is. See docs/configure.md."
        ) from exc


def _port(suffix: str, default: int, protocol: str) -> int:
    """A port, refused **here** rather than by the socket layer.

    `NETCORENOC_HTTP_PORT=99999` used to reach `sock.bind()` and come back as
    `OverflowError: bind(): port must be 0-65535` from inside asyncio, after the appliance had
    already logged that it was listening. The range is a property of a port, so it belongs with
    the reader that produces one.
    """
    value = _number(suffix, str(default), int, f"a whole number of a {protocol} port")
    if not 1 <= value <= 65535:
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value} is outside the range of a {protocol} port (1-65535). "
            f"Unset it to use the default ({default}). See docs/configure.md."
        )
    return value


def _tls(cert: str, key: str) -> None:
    """Both or neither, and both readable — refused by name (F69).

    Setting only one was worse than a plain failure: `tls_enabled` requires **both**, so the
    appliance decided it was serving plain HTTP — telling admins so through `operator_warnings` —
    while uvicorn was handed the certificate anyway and died on it. One setting, two components
    disagreeing about what it meant.
    """
    if bool(cert) != bool(key):
        present, missing = ("TLS_CERT", "TLS_KEY") if cert else ("TLS_KEY", "TLS_CERT")
        raise SettingsError(
            f"{ENV_PREFIX}{present} is set and {ENV_PREFIX}{missing} is not. Built-in TLS needs "
            f"both; set {ENV_PREFIX}{missing}, or unset {ENV_PREFIX}{present} to serve plain HTTP. "
            f"See docs/configure.md."
        )
    for suffix, path in (("TLS_CERT", cert), ("TLS_KEY", key)):
        if path and not os.access(path, os.R_OK):
            raise SettingsError(
                f"{ENV_PREFIX}{suffix}={path!r} cannot be read. It is a path **inside the "
                f"container or on the host the appliance runs on**; check the mount and the "
                f"permissions. See docs/configure.md."
            )


@dataclass(frozen=True)
class Settings:
    """All configuration is environment variables with sane defaults — nothing else."""

    db_path: str = "netcorenoc.db"
    trap_host: str = "0.0.0.0"  # nosec B104 - a trap destination must listen externally
    trap_port: int = 162
    http_host: str = "0.0.0.0"  # nosec B104 - the UI/API serve the operator LAN
    http_port: int = 8080
    allowlist: str = ""
    api_token: str = ""
    retention_days: float = 7.0
    audit_retention_days: float = 365.0
    tls_cert: str = ""
    tls_key: str = ""
    log_json: bool = False
    # Removed OPTICORR_* variables found in the environment (names only, never values). Captured
    # here so `run()` can fail loud with the exact replacement for each (v0.6.0, DECISIONS #45).
    legacy_env: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        def s(suffix: str, default: str) -> str:
            got = read_env(suffix, default)
            return got if got is not None else default

        tls_cert, tls_key = s("TLS_CERT", cls.tls_cert), s("TLS_KEY", cls.tls_key)
        _tls(tls_cert, tls_key)
        return cls(
            db_path=s("DB", cls.db_path),
            trap_host=s("TRAP_HOST", cls.trap_host),
            trap_port=_port("TRAP_PORT", cls.trap_port, "UDP"),
            http_host=s("HTTP_HOST", cls.http_host),
            http_port=_port("HTTP_PORT", cls.http_port, "TCP"),
            allowlist=s("ALLOWLIST", cls.allowlist),
            # The shared API token was removed in v0.3.0; setting it is a hard error at startup
            # (run()). The legacy-prefixed name is caught by the v0.6.0 alias check instead.
            api_token=os.environ.get(ENV_PREFIX + "API_TOKEN", cls.api_token),
            retention_days=_number(
                "RETENTION_DAYS", str(cls.retention_days), float, "a number of days"
            ),
            audit_retention_days=_number(
                "AUDIT_RETENTION_DAYS", str(cls.audit_retention_days), float, "a number of days"
            ),
            tls_cert=tls_cert,
            tls_key=tls_key,
            log_json=s("LOG_JSON", "") not in ("", "0", "false", "False"),
            legacy_env=tuple(legacy_env_names()),
        )

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)


class LegacyTokenRemovedError(RuntimeError):
    """The shared API token was removed in v0.3.0 (§5.8); setting it is now a hard error."""


class LegacyEnvRemovedError(RuntimeError):
    """An `OPTICORR_*` environment alias was removed in v0.6.0; setting one is a hard error."""


def legacy_env_error(names: tuple[str, ...]) -> LegacyEnvRemovedError:
    """The startup error for removed aliases: every variable named, no value ever printed."""
    mapping = "\n".join(
        f"  {name} -> {ENV_PREFIX}{name[len(LEGACY_ENV_PREFIX) :]}" for name in names
    )
    return LegacyEnvRemovedError(
        f"the legacy {LEGACY_ENV_PREFIX}* environment aliases were removed in v0.6.0 "
        f"(deprecated since v0.4.0). Rename and unset:\n{mapping}\n"
        "See MIGRATION.md. Refusing to start rather than ignoring them, because a silently "
        "ignored setting (an allowlist, a TLS path) is a security regression, not a nuisance."
    )
