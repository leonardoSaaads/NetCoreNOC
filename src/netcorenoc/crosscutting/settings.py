"""Process configuration: the environment is the whole configuration surface.

`Settings` is read once at startup and never mutated. Everything an operator can set is an
environment variable with a sane default — no config file, no flags, no runtime reconfiguration
beyond the two values `runtime.RuntimeConfig` holds.

The removed-alias errors live here too, beside the reader that would otherwise have ignored them:
a silently ignored setting (an allowlist, a TLS path) is a security regression, not a nuisance.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

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


def _non_negative(suffix: str, default: float) -> float:
    """A duration in seconds, refused by name when it is not a number or is negative."""
    value = _number(suffix, str(default), float, "a number of seconds")
    if value < 0 or value != value:  # NaN is not a duration either
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value} is not a duration (0 or more seconds). Unset it to use "
            f"the default ({default}). See docs/configure.md."
        )
    return value


def _positive(suffix: str, default: float) -> float:
    """A quantity that must be more than zero and finite (a KPI of 0 s is every trap late)."""
    value = _number(suffix, str(default), float, "a number of seconds")
    if not value > 0 or value == float("inf"):  # NaN, 0, negatives and infinity are refused
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value} is not a duration above 0 seconds. Unset it to use the "
            f"default ({default}). See docs/configure.md."
        )
    return value


def _choice(suffix: str, default: int, allowed: tuple[int, ...]) -> int:
    """A whole number from a short list, refused by name otherwise."""
    value = _number(suffix, str(default), int, "a whole number")
    if value not in allowed:
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value} is not one of {', '.join(map(str, allowed))}. Unset it "
            f"to use the default ({default}). See docs/configure.md."
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


def _database_url(url: str, password_file: str, name: str = "DATABASE") -> str:
    """The PostgreSQL URL, with the password from ``password_file`` when one is named (ADR #452).

    The file is how a Docker or Kubernetes secret reaches the process without sitting in its
    environment, where ``docker inspect`` and ``/proc/<pid>/environ`` would show it.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise SettingsError(
            f"{ENV_PREFIX}{name}_URL must be a postgresql:// URL (got scheme {parts.scheme!r}). "
            "See docs/configure.md."
        )
    if not password_file:
        return url
    try:
        password = Path(password_file).read_text().strip()
    except OSError as exc:
        raise SettingsError(
            f"{ENV_PREFIX}{name}_PASSWORD_FILE={password_file!r} cannot be read ({exc.strerror})."
            " It is a path inside the container or on the host; check the mount and the "
            "permissions. See docs/configure.md."
        ) from exc
    user = quote(parts.username or "", safe="")
    host = parts.netloc.rsplit("@", 1)[-1]
    netloc = f"{user}:{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _identifier(suffix: str, value: str) -> str:
    """A role or schema name: lower-case letters, digits and _, or empty."""
    if value and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value!r} is not a role name: lower-case letters, digits and _, "
            "starting with a letter or _. See docs/configure.md."
        )
    return value


def _without_query_password(query: str) -> tuple[str, str | None]:
    """``query`` without a ``password=`` parameter (libpq and the driver both accept one), and its
    value. Untouched, byte for byte, when there is none."""
    pairs = parse_qsl(query, keep_blank_values=True)
    found = [value for key, value in pairs if key == "password"]
    if not found:
        return query, None
    return urlencode([(key, value) for key, value in pairs if key != "password"]), found[-1]


def split_password(url: str) -> tuple[str, str | None]:
    """``(url_without_password, password)``: for a child process that takes the password in its
    environment (``PGPASSWORD``) rather than on a command line ``ps`` can read. Either place a
    URL may carry it — before the host, or as ``?password=`` — is emptied."""
    parts = urlsplit(url)
    query, from_query = _without_query_password(parts.query)
    password = unquote(parts.password) if parts.password is not None else from_query
    if password is None:
        return url, None
    host = parts.netloc.rsplit("@", 1)[-1]
    user = f"{parts.username}@" if parts.username else ""
    return urlunsplit((parts.scheme, f"{user}{host}", parts.path, query, parts.fragment)), password


def redact_url(url: str) -> str:
    """A database URL fit for a log line or a settings screen: the password replaced by ***."""
    parts = urlsplit(url)
    query, from_query = _without_query_password(parts.query)
    if parts.password is None and from_query is None:
        return url
    host = parts.netloc.rsplit("@", 1)[-1]
    user = f"{parts.username}@" if parts.username else ""
    user = f"{parts.username}:***@" if parts.password is not None else user
    query = "&".join(filter(None, [query, "password=***" if from_query is not None else ""]))
    return urlunsplit((parts.scheme, f"{user}{host}", parts.path, query, parts.fragment))


def _count(suffix: str, default: int, low: int, high: int) -> int:
    """A whole number in ``[low, high]``, refused by name otherwise."""
    value = _number(suffix, str(default), int, "a whole number")
    if not low <= value <= high:
        raise SettingsError(
            f"{ENV_PREFIX}{suffix}={value} is outside {low}-{high}. Unset it to use the default "
            f"({default}). See docs/configure.md."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """All configuration is environment variables with sane defaults — nothing else."""

    # ADR #452: the PostgreSQL database and the schema in it. The password may come from a file
    # (a Docker secret) instead of the URL; `database_url` is then the URL with it filled in, so
    # only `redact_url(database_url)` is ever logged or shown.
    database_url: str = "postgresql://netcorenoc@localhost:5432/netcorenoc"
    db_schema: str = "netcorenoc"
    # Connections API reads run on, besides the one writer; and asyncpg's prepared-statement cache
    # per connection (0 disables it, for a proxy that cannot keep prepared statements). A pooler in
    # transaction mode is unsupported either way: the instance lock is a session lock (ADR #452).
    db_readers: int = 4
    db_statement_cache: int = 1024
    # ADR #453: the schema owner, when it is not the role the appliance runs as. Set, migrations
    # run as this role at start and the appliance runs as `database_url`'s, which may not alter,
    # drop or truncate anything; unset, one role does both.
    database_owner_url: str = ""
    # ADR #453: the runtime role `db migrate` grants the appliance's privileges to, when it runs
    # as the owner and the appliance runs as another role (the Compose recipe).
    db_runtime_role: str = ""
    # Files the appliance keeps on its own disk: the trap spool (ADR #450) and the secret key
    # (ADR #453), which the database server never sees.
    data_dir: str = "."
    secret_key_file: str = ""
    # ADR #455: the second key a device credential is sealed under, kept apart from the first; and
    # how often the vault checks that each credential's endpoint answers (0: never; ADR #457).
    secrets_key_file: str = ""
    noc_check_s: float = 300.0
    # `NETCORENOC_DB`, the SQLite file of v0.30.0 and earlier: set, it stops the start (ADR #452).
    legacy_db: str = ""
    trap_host: str = "0.0.0.0"  # nosec B104 - a trap destination must listen externally
    trap_port: int = 162
    http_host: str = "0.0.0.0"  # nosec B104 - the UI/API serve the operator LAN
    http_port: int = 8080
    allowlist: str = ""
    api_token: str = ""
    retention_days: float = 7.0
    audit_retention_days: float = 365.0
    # v0.28.0 (ADR #431): how long an active alarm may be silent before its next trap is a new
    # occurrence rather than a repeat. 0 turns the rule off.
    rearm_s: float = 3600.0
    # ADR #451: the classification KPI's defaults — each trap classified within the target of its
    # own arrival, and never later than the limit. Counted, never enforced by discarding a trap
    # (`engine/operate/deadline.py`); an admin's saved values override both.
    kpi_target_s: float = 60.0
    kpi_limit_s: float = 120.0
    # ADR #449: 1 receives and decodes traps in a separate process; 0 keeps the receiver in the
    # main process, without the spool (development, and hosts without POSIX descriptors).
    ingest_processes: int = 1
    # ADR #450: where accepted traps are spooled before they are classified ("" is a `spool`
    # directory in `data_dir`), and how large the spool may grow.
    spool_dir: str = ""
    spool_max_mb: float = 2048.0
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
        target = _positive("KPI_TARGET_S", cls.kpi_target_s)
        limit = _positive("KPI_LIMIT_S", cls.kpi_limit_s)
        if target > limit:
            raise SettingsError(
                f"{ENV_PREFIX}KPI_TARGET_S={target} is later than {ENV_PREFIX}KPI_LIMIT_S={limit}. "
                "The target is where a trap should be classified by; the limit, the latest it may "
                "be. See docs/configure.md."
            )
        schema = s("DB_SCHEMA", cls.db_schema)
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise SettingsError(
                f"{ENV_PREFIX}DB_SCHEMA={schema!r} is not a schema name: lower-case letters, "
                "digits and _, starting with a letter or _. See docs/configure.md."
            )
        return cls(
            database_url=_database_url(
                s("DATABASE_URL", cls.database_url), s("DATABASE_PASSWORD_FILE", "")
            ),
            db_schema=schema,
            database_owner_url=(
                _database_url(owner, s("DATABASE_OWNER_PASSWORD_FILE", ""), "DATABASE_OWNER")
                if (owner := s("DATABASE_OWNER_URL", ""))
                else ""
            ),
            db_runtime_role=_identifier("DB_RUNTIME_ROLE", s("DB_RUNTIME_ROLE", "")),
            db_readers=_count("DB_READERS", cls.db_readers, 1, 64),
            db_statement_cache=_count("DB_STATEMENT_CACHE", cls.db_statement_cache, 0, 100_000),
            data_dir=s("DATA_DIR", cls.data_dir),
            secret_key_file=s("SECRET_KEY_FILE", cls.secret_key_file),
            secrets_key_file=s("SECRETS_KEY_FILE", cls.secrets_key_file),
            noc_check_s=_non_negative("NOC_CHECK_S", cls.noc_check_s),
            legacy_db=s("DB", cls.legacy_db),
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
            rearm_s=_non_negative("REARM_S", cls.rearm_s),
            kpi_target_s=target,
            kpi_limit_s=limit,
            ingest_processes=_choice("INGEST_PROCESSES", cls.ingest_processes, (0, 1)),
            spool_dir=s("SPOOL_DIR", cls.spool_dir),
            spool_max_mb=_positive("SPOOL_MAX_MB", cls.spool_max_mb),
            tls_cert=tls_cert,
            tls_key=tls_key,
            log_json=s("LOG_JSON", "") not in ("", "0", "false", "False"),
            legacy_env=tuple(legacy_env_names()),
        )

    @property
    def spool_path(self) -> Path:
        """`NETCORENOC_SPOOL_DIR`, or a ``spool`` directory in `NETCORENOC_DATA_DIR` (ADR #450)."""
        return Path(self.spool_dir or Path(self.data_dir) / "spool").resolve()

    @property
    def secret_key_path(self) -> Path:
        """`NETCORENOC_SECRET_KEY_FILE`, or ``secret.key`` in `NETCORENOC_DATA_DIR` (ADR #453)."""
        return Path(self.secret_key_file or Path(self.data_dir) / "secret.key").resolve()

    @property
    def secrets_key_path(self) -> Path:
        """`NETCORENOC_SECRETS_KEY_FILE`, or ``secrets.key`` in `NETCORENOC_DATA_DIR` (ADR #455)."""
        return Path(self.secrets_key_file or Path(self.data_dir) / "secrets.key").resolve()

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
