"""The correlator's entry point: ``python -m netcorenoc.main``.

**This stays a module, not a package** (DECISIONS #89). `README.md`, the `Makefile`, the
`Dockerfile`, `docker-compose.yml` and `MIGRATION.md` all print `python -m netcorenoc.main`, and
turning this into a package would need `main/__main__.py` and change the semantics of the one
command every operator types — a behaviour change wearing a structural hat.

Everything below the entry point is a **re-export**. `netcorenoc.main` has been the import path
since v0.1.0 and stays it: `from netcorenoc.main import Engine, Settings, run` keeps working
verbatim, so the v0.7.3 split is invisible from outside. Where the code actually lives:

* `engine.py` — the `Engine`, the batch lock, and the whole ingest path
* `runner.py` — `run()`, the `Supervisor`, and the process wiring
* `settings.py` — `Settings` and the environment
* `maintenance.py`, `gaps.py`, `scorer_lifecycle.py` — the periodic work off the ingest path
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from netcorenoc.crosscutting.logsetup import configure_logging
from netcorenoc.crosscutting.settings import (
    ENV_PREFIX,
    LEGACY_ENV_PREFIX,
    LegacyEnvRemovedError,
    LegacyTokenRemovedError,
    Settings,
    SettingsError,
    legacy_env_error,
    legacy_env_names,
    read_env,
)
from netcorenoc.engine.operate.engine import IDLE_CLOSE_S, Engine, FlapDetector
from netcorenoc.engine.operate.gaps import GAP_CLOSE_S, GapTracker
from netcorenoc.runner import HttpServerStartError, Supervisor, operator_warnings, run

# The re-export surface, written down rather than inherited by accident: `mypy --strict` forbids
# implicit re-export, which makes this list the explicit statement of what `netcorenoc.main` is.
# Everything here appeared in the v0.7.3 Gate 0 import inventory and must keep resolving.
__all__ = [
    "ENV_PREFIX",
    "GAP_CLOSE_S",
    "IDLE_CLOSE_S",
    "LEGACY_ENV_PREFIX",
    "Engine",
    "FlapDetector",
    "GapTracker",
    "HttpServerStartError",
    "LegacyEnvRemovedError",
    "LegacyTokenRemovedError",
    "Settings",
    "SettingsError",
    "Supervisor",
    "legacy_env_error",
    "legacy_env_names",
    "main",
    "operator_warnings",
    "read_env",
    "run",
]


#: The startup refusals an operator is expected to read and act on, as opposed to the ones that
#: mean this code is wrong. Each carries a sentence naming the setting and what to do about it, so
#: printing the sentence and exiting 2 is more useful than twenty frames of traceback above it
#: (F69). Anything not listed here still gets its full traceback, deliberately: an unexpected
#: exception is a defect and the frames are the report.
_OPERATOR_ERRORS = (
    SettingsError,
    LegacyEnvRemovedError,
    LegacyTokenRemovedError,
    HttpServerStartError,
)


def main() -> None:
    try:
        _main()
    except _OPERATOR_ERRORS as exc:
        logging.getLogger("netcorenoc").error("%s", exc)
        raise SystemExit(2) from None


def _main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_json)
    loop_main = asyncio.new_event_loop()
    task = loop_main.create_task(run(settings))
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop_main.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        loop_main.run_until_complete(task)


if __name__ == "__main__":
    main()
