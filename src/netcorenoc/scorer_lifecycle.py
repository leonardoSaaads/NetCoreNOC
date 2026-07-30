"""The v0.6.0 scoring seam's **lifecycle**, as distinct from scoring itself.

Loading, validating, failing safe, warning the operator, and auditing a degradation. The formula
lives in `scoring.py`; what is in effect, and what happens when the stored configuration cannot be
trusted, lives here.

Fail-safe throughout: an unreadable store, a dangling pointer, an unsupported contract version or
an out-of-bounds row leaves the **coded defaults** in place and raises an operator warning. The
engine can never run on an unvalidated formula, and never refuses to run for want of one.
"""

from __future__ import annotations

import logging

from netcorenoc import audit, scoring
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
        except Exception as exc:  # a config read must never stop correlation
            self._loaded_key = None  # retry on the next pass; the DB may come back
            self._use_default_scorer(f"scoring configuration unreadable ({type(exc).__name__})")
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
        self.scorer_warnings = []

    def _use_default_scorer(self, reason: str) -> None:
        """Fall back to the coded defaults and tell the operator why (never silently)."""
        warning = f"{reason}. Correlation is running on the built-in default parameters."
        if self.scorer_warnings != [warning]:
            log.warning("%s; using the built-in default scoring parameters", reason)
        self.correlator.set_scorer(scoring.default_scorer())
        self.scorer_config_id = None
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
