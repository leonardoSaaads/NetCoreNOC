"""The v0.6.0 scoring seam's **lifecycle**, as distinct from scoring itself.

Loading, validating, failing safe, warning the operator, and auditing a degradation. The formula
lives in `scoring.py`; what is in effect, and what happens when the stored configuration cannot be
trusted, lives here.

Fail-safe throughout: an unreadable store, a dangling pointer, an unsupported contract version or
an out-of-bounds row leaves the **coded defaults** in place and raises an operator warning. The
engine can never run on an unvalidated formula, and never refuses to run for want of one.

## v0.11.0 — dispatch by kind, which this module has never had

Before this release `load_scorer_config` constructed `scoring.AdditiveScorer(...)`
**unconditionally** and read the `scorer_id` column only as a label — measured by `ast` in
`docs/gates/v0.11.0-phase-0.md` §2.2, not asserted. Now the active pointer may name a
`model_version` of a declared kind, and this module asks `netcorenoc.model_version` which scorer a
row describes rather than deciding for itself.

**Every new failure resolves the way every old one did.** A payload that cannot be parsed, a kind
this build does not know, a contract version it does not support, and a parameter set the per-kind
validator rejects all take `_use_default_scorer`: fall back to the built-in default, warn the
operator, write an audit row. **The load path never raises**, and it never silently continues with a
half-understood scorer.

**And it does not fall back to the previous model.** `PREREGISTRATION-0.11.0.md` §6.9 registers that
in advance: an appliance silently running something other than what the pointer names is worse than
one running the documented default, because the first is invisible and the second is in the
operator's warnings.
"""

from __future__ import annotations

import logging

from netcorenoc import audit, model_version, scoring
from netcorenoc.engine_base import EngineBase

log = logging.getLogger("netcorenoc")


class ScorerLifecycleMixin(EngineBase):
    async def load_scorer_config(self) -> None:
        """Load the active scoring configuration — **the reload point** (§DESIGN v0.6.0).

        Called at :meth:`start` and at the top of each :meth:`maintenance` pass, so an admin's
        apply or rollback takes effect within one maintenance interval and *never* mid-batch: a
        batch is always scored by exactly one configuration, which is what makes the `config_id`
        recorded on a situation the one that actually scored it.

        Fail-safe: an unreachable store, a dangling pointer, an unsupported contract version, or
        an out-of-bounds stored row leaves the **coded defaults** in place and raises an operator
        warning. The engine can never run with an unvalidated formula, and never refuses to run
        for want of one.
        """
        try:
            row = await self.store.active_scorer_config()
            model_row = None if row is not None else await self.store.active_model_version()
        except Exception as exc:  # a config read must never stop correlation
            self._loaded_key = None  # retry on the next pass; the DB may come back
            self._use_default_scorer(f"scoring configuration unreadable ({type(exc).__name__})")
            return

        # THE POINTER NAMES A MODEL VERSION. The database's CHECK makes "both at once" impossible,
        # so this branch is reached only when `config_id` is NULL — the `if row is not None` above
        # is belt to that brace and costs one comparison at a reload point, never per pair.
        if model_row is not None:
            self._load_model_version(model_row)
            return

        key = None if row is None else (int(row["id"]), str(row["params_hash"]))
        if key == self._loaded_key:
            # Unchanged since the last reload: leave the live scorer alone. This is what makes a
            # degradation *sticky* — re-instantiating the same configuration every maintenance
            # pass would silently un-degrade a scorer that has already proven it fails.
            return
        self._loaded_key = key
        if row is None:
            # No pointer yet (a store older than the seed, or a bare test fixture): coded
            # defaults, silently — this is the documented zero-config state, not a failure.
            self.correlator.set_scorer(scoring.default_scorer())
            self.scorer_config_id = None
            self.scorer_model_version_id = None
            self.scorer_warnings = []
            return
        try:
            scoring.check_contract_version(str(row["contract_version"]))
            scoring.validate_params(
                float(row["w_t"]),
                float(row["w_a"]),
                float(row["w_e"]),
                float(row["tau_s"]),
                float(row["threshold"]),
            )
        except (scoring.ScorerParamsError, scoring.ContractVersionError) as exc:
            self._use_default_scorer(f"stored scoring configuration rejected: {exc}")
            return
        self.correlator.set_scorer(
            scoring.AdditiveScorer(
                w_t=float(row["w_t"]),
                w_a=float(row["w_a"]),
                w_e=float(row["w_e"]),
                tau_s=float(row["tau_s"]),
                threshold=float(row["threshold"]),
                scorer_id=str(row["scorer_id"]),
                contract_version=str(row["contract_version"]),
            )
        )
        self.scorer_config_id = int(row["id"])
        self.scorer_model_version_id = None
        self.scorer_warnings = []

    def _load_model_version(self, row: dict[str, object]) -> None:
        """Activate the scorer a `model_version` row describes, or fall back. **Never raises.**

        The `try` is deliberately wide. `ModelPayloadError` covers every rejection this build
        anticipates — bad JSON, an unknown kind, an unsupported contract version, a degenerate
        parameter set — and the bare `Exception` covers the ones it does not: a row whose columns
        are the wrong type, a `None` where a string belongs, a future kind whose constructor raises
        for a reason nobody has thought of yet. **Ambiguity about whether a payload is understood
        resolves to "it is not"**, and the consequence of that resolution is always the same one.

        A narrower `except ModelPayloadError` would be the more precise-looking code and would let
        exactly the unanticipated failure — the one this guard exists for — take ingestion down.
        """
        key = (int(row["id"]), str(row["params_hash"]))  # type: ignore[call-overload]
        if key == self._loaded_key:
            return  # unchanged since the last reload; leave the live scorer (and any degradation)
        self._loaded_key = key
        try:
            scorer = model_version.scorer_for(
                str(row["kind"]), str(row["contract_version"]), str(row["params_document"])
            )
        except Exception as exc:
            reason = (
                exc.args[0]
                if isinstance(exc, model_version.ModelPayloadError) and exc.args
                else f"{type(exc).__name__}"
            )
            self._use_default_scorer(f"stored model version {key[0]} rejected: {reason}")
            return
        self.correlator.set_scorer(scorer)
        self.scorer_config_id = None
        self.scorer_model_version_id = key[0]
        self.scorer_warnings = []

    def _use_default_scorer(self, reason: str) -> None:
        """Fall back to the coded defaults and tell the operator why (never silently)."""
        warning = f"{reason}. Correlation is running on the built-in default parameters."
        if self.scorer_warnings != [warning]:
            log.warning("%s; using the built-in default scoring parameters", reason)
        self.correlator.set_scorer(scoring.default_scorer())
        self.scorer_config_id = None
        # Cleared too, and this is the line that makes §6.9 true: the fallback goes to the BUILT-IN
        # DEFAULT, never to the previously-loaded model. Leaving this set would leave the engine
        # claiming to run a model it had just refused to load.
        self.scorer_model_version_id = None
        self.scorer_warnings = [warning]

    def scorer_warning_list(self) -> list[str]:
        """Operator warnings from the scoring path: a rejected config, or a degraded scorer."""
        return [*self.scorer_warnings, *self.correlator.scorer.warnings()]

    async def _audit_scorer_fallback(self, now: float) -> None:
        """Record `scorer.fallback` once when the active scorer degrades to the defaults."""
        safe = self.correlator.scorer
        if not safe.degraded or safe.audited:
            return
        safe.audited = True
        await audit.write_event(
            self.store,
            ts=now,
            actor="system",
            role=None,
            source_ip=None,
            action="scorer.fallback",
            outcome="error",
            object_type="scorer_config",
            object_id=str(self.scorer_config_id) if self.scorer_config_id else None,
            details={"reason": safe.last_error, "failures": safe.failures},
        )
