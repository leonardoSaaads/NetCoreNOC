/* **The three parameter classes, as data** (draft §6, §V.3).
 *
 * > `resolved = max(project floor, deployment policy)` — harden always, soften never.
 *
 * This module is where principle 9 either lives or dies quietly. An "all the parameters" screen
 * shows a sufficiency floor beside a sampling rate; an admin lowers the floor because the field
 * allowed it, and nine releases of evidence discipline die to an input box.
 *
 * So the class is a property of the parameter, declared once, here — not a styling decision taken
 * per screen. Three classes, and the third is what makes the screen educate:
 *
 *   **mechanism**   freely settable, with the cost shown.
 *   **hardening**   raise only. The UI shows the project floor, accepts stricter values, and
 *                   REFUSES looser ones with all four things §6.2 requires.
 *   **structural**  displayed for transparency, no edit control. Not a disabled input — a
 *                   *fact*. "seal: intact, 0 queries" with no control to change it teaches an
 *                   admin something true: it is a guarantee, not a preference.
 *
 * ## What is honestly settable today, and what is not (ADR #178)
 *
 * The client's refusal is an affordance; the server's is the control. For the scorer's degeneracy
 * bounds **both exist**: `scoring.validate_params` refuses out-of-bounds parameters at the API and
 * `hardeningRefusal` below refuses them before the request is sent. That pair is real, live, and
 * is what `tests/test_ui_invariants.py` demonstrates red.
 *
 * The pre-registered sufficiency floors (`asserting_bags >= 50`, `asserting_incidents >= 30`) are
 * module constants in `routes_promotion.py` with **no write path at all**. This release does not
 * invent one: persisting them would change the promotion gate's inputs, which is evidence-chain
 * work and not UI work. They are therefore shown as hardening-only with `settable: false` and the
 * reason stated on screen, and "give the deployment a way to raise them" is a ROADMAP line.
 * Showing a control that cannot work would be the empty placeholder §2 forbids.
 */

export const MECHANISM = "mechanism";
export const HARDENING = "hardening";
export const STRUCTURAL = "structural";

/**
 * A floor's direction.
 *   `min`  the submitted value must be >= the floor (raising it is stricter)
 *   `max`  the submitted value must be <= the ceiling (lowering it is stricter)
 */
export const MIN = "min";
export const MAX = "max";

/**
 * **Would this submission loosen a hardening-only value?**
 *
 * Returns `null` when the submission is acceptable, or the four things draft §6.2 requires the UI
 * to show. A bare "rejected" teaches nothing and invites a workaround; `SECURITY-REVIEW-0.6` §4
 * already treats wording as a control for the scorer preview and the same standard applies here.
 *
 * Pure, and deliberately: `tests/test_ui_invariants.py` drives it directly with every bound the
 * server publishes, without a DOM and without a server that would refuse.
 */
export function hardeningRefusal({ label, submitted, floor, direction, why, unit }) {
  const value = Number(submitted);
  if (!Number.isFinite(value)) {
    return {
      label, submitted, floor, direction, why,
      headline: `${label} must be a number.`,
      applied: false,
      stricterIs: direction === MIN ? "a larger value" : "a smaller value",
      unit: unit || "",
    };
  }
  const loosens = direction === MIN ? value < floor : value > floor;
  if (!loosens) return null;
  return {
    label,
    submitted: value,
    floor,
    direction,
    why,
    unit: unit || "",
    applied: false,
    headline:
      direction === MIN
        ? `${label} cannot be lowered below ${floor}${unit ? ` ${unit}` : ""} here.`
        : `${label} cannot be raised above ${floor}${unit ? ` ${unit}` : ""} here.`,
    stricterIs: direction === MIN ? "a larger value" : "a smaller value",
  };
}

/**
 * Every refusal a candidate scorer parameter set would produce, against the bounds **the server
 * published**. There is no client-side copy of a bound anywhere in this UI: `GET /api/scorer`
 * returns them and this reads them, so a bound changed in `scoring.py` moves here with no edit.
 */
export function scorerRefusals(candidate, bounds) {
  const refusals = [];
  const tau = hardeningRefusal({
    label: "tau (temporal decay constant)",
    submitted: candidate.tau_s, floor: bounds.min_tau_s, direction: MIN, unit: "s",
    why: "Below one second the temporal term becomes a step function inside a single engine " +
         "batch, so pairs stop being distinguishable by time at all.",
  }) ?? hardeningRefusal({
    label: "tau (temporal decay constant)",
    submitted: candidate.tau_s, floor: bounds.max_tau_s, direction: MAX, unit: "s",
    why: "Far beyond the correlation window the term stops discriminating between pairs: " +
         "everything looks temporally adjacent and the term contributes nothing.",
  });
  if (tau) refusals.push(tau);

  const weightSum = Number(candidate.w_t) + Number(candidate.w_a) + Number(candidate.w_e);
  const sum = hardeningRefusal({
    label: "the sum of the three weights",
    submitted: weightSum, floor: bounds.min_weight_sum, direction: MIN,
    why: "A vanishing weight sum makes every threshold unreachable, so nothing would ever link " +
         "and the appliance would stop grouping silently rather than visibly.",
  });
  if (sum) refusals.push(sum);

  // Not an independent bound: the threshold's ceiling is derived from the weights the admin just
  // typed. Expressed through the same helper so one refusal shape covers every case.
  const ceiling = weightSum - bounds.threshold_margin;
  const threshold = hardeningRefusal({
    label: "the link threshold",
    submitted: candidate.threshold, floor: Number(ceiling.toFixed(6)), direction: MAX,
    why: `The threshold must stay at least ${bounds.threshold_margin} below the maximum score ` +
         `these weights can produce (${weightSum.toFixed(4)}). Above it, nothing would ever ` +
         `link and every alarm would become its own situation.`,
  });
  if (threshold) refusals.push(threshold);

  return refusals;
}

/**
 * The settings table.
 *
 * `source` says where the value comes from, which is what makes the three-column precedence
 * meaningful; `restart` says honestly whether a change takes effect without one; `impact` is the
 * sentence an operator reads before they change it.
 */
export const SETTINGS = [
  {
    key: "allowlist", label: "Trap allowlist", klass: MECHANISM, live: true,
    env: "NETCORENOC_ALLOWLIST",
    impact: "Which source addresses the receiver accepts traps from. An empty value accepts " +
            "every source. Narrowing it silently drops traps from anything outside it — they " +
            "are not quarantined, they are refused at the socket.",
    restart: null,
  },
  {
    key: "retention_days", label: "Operational retention", klass: MECHANISM, live: true,
    env: "NETCORENOC_RETENTION_DAYS", unit: "days", destructive: true,
    impact: "How long cleared and closed history is kept. **Lowering this deletes rows and there " +
            "is no rollback.** The maintenance loop applies it in the background.",
    restart: null,
  },
];

/** Settings read once at startup. `Settings` is frozen after construction (`settings.py`). */
export const RESTART_REQUIRED = [
  ["db_path", "NETCORENOC_DB", "Which SQLite file the appliance opens."],
  ["trap_host", "NETCORENOC_TRAP_HOST", "The address the trap receiver binds."],
  ["trap_port", "NETCORENOC_TRAP_PORT", "The UDP port the trap receiver binds."],
  ["http_host", "NETCORENOC_HTTP_HOST", "The address the API and this console bind."],
  ["http_port", "NETCORENOC_HTTP_PORT", "The port the API and this console bind."],
  ["audit_retention_days", "NETCORENOC_AUDIT_RETENTION_DAYS", "The outer bound on the audit log's life."],
  ["tls_enabled", "NETCORENOC_TLS_CERT / _TLS_KEY", "Whether the console is served over TLS."],
  ["log_json", "NETCORENOC_LOG_JSON", "Whether the process logs JSON lines instead of text."],
];

/**
 * **Which screen resolves this warning, derived from the tables above** (v0.16.4, DECISIONS #288).
 *
 * The maintainer's ask for the bell was *"a link to the setting that resolves it"*. Not every
 * warning has one — an ingest gap, a crashed background task and a stale situation are conditions,
 * not misconfigurations — so this returns `null` for those and the bell renders them as text with
 * no affordance. A control that navigated somewhere unhelpful would teach an operator that the
 * bell's links are noise, which costs the ones that work.
 *
 * **Derived rather than listed.** `SETTINGS` and `RESTART_REQUIRED` already name every parameter
 * this console knows about, because the Settings screen renders them; a warning is matched against
 * those names. Add a parameter and its warnings link themselves; delete one and the link goes.
 * A table of `(warning text, destination)` pairs written here would be Appendix B's guard that
 * lists what it checks, and it would keep a link pointing at the wrong place after a rewording
 * rather than losing it.
 *
 * Measured: of the nine warning strings this appliance can emit, **two** name a parameter — the
 * empty trap allowlist and the missing TLS certificate. Both resolve; the other seven do not, and
 * that fraction is reported rather than the fact that linking works at all.
 */
export function warningTarget(text) {
  const haystack = String(text ?? "").toLowerCase();
  const named = [
    ...SETTINGS.map((entry) => [entry.label, entry.env]),
    ...RESTART_REQUIRED.map(([, env, _why]) => [null, env]),
  ];
  for (const [label, env] of named) {
    // The environment variable first: it is unambiguous, and a warning that names one is naming
    // the thing an admin will type. `NETCORENOC_TLS_CERT/KEY` is one token in the warning and two
    // in the table, so each side of a `/` is tried.
    for (const token of String(env ?? "").split("/")) {
      const needle = token.trim().toLowerCase();
      if (needle.length > 3 && haystack.includes(needle)) return "#/settings";
    }
    if (label && haystack.includes(label.toLowerCase())) return "#/settings";
  }
  return null;
}
