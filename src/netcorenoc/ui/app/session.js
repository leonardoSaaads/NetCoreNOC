/* The resolved capability set, and the single question every gate asks.
 *
 * F28 at the UI layer, unchanged from v0.12.0 and restated because the rewrite is exactly when it
 * would be lost: affordances are gated on the **resolved** set the server returned from
 * `/api/me`, never on role rank. An admin may have stored a policy narrowing what a role holds,
 * and a UI that re-derived permissions from rank would be a second decision site that silently
 * disagrees with the server.
 *
 * ## `can()` is an affordance, never a control
 *
 * Draft §11.3, and it is worth being blunt about: every `can()` in this UI hides something. None
 * of them protects anything. The server refuses what a principal may not do, and
 * `tests/test_rbac.py` is where that is proven. If this module returned `true` for everything,
 * the appliance would still be secure and the console would merely be dishonest — offering
 * controls the server rejects. That is the correct division and it is why nothing here is
 * described as a check.
 */

let current = null;
let policy = null;

/** `{ user, role, capabilities: Set, scope, mustChangePassword }`, or null when signed out. */
export function session() { return current; }

export function setSession(me) {
  current = me === null ? null : {
    user: me.user,
    role: me.role,
    capabilities: new Set(me.capabilities || []),
    scope: me.scope || { scoped: false, ne_count: null },
    mustChangePassword: !!me.must_change_password,
  };
  if (me && me.password_policy) setPasswordPolicy(me.password_policy);
  return current;
}

/* The password policy the SERVER enforces, held here for the same reason `capabilities` is: so no
 * screen carries a second copy of a rule the appliance owns (v0.15.3, DECISIONS #240). It arrives
 * on `/api/me`, and — because the forced first change happens before any session exists — also on
 * the `must_change_password` response from `/api/login`.
 *
 * `null` until the server has said, and every consumer renders nothing rather than guessing. A
 * default of 12 here would be the second source of truth this exists to avoid. */
export function setPasswordPolicy(next) {
  policy = next && typeof next.min === "number" && typeof next.max === "number"
    ? { min: next.min, max: next.max, rule: String(next.rule || "length") }
    : null;
  return policy;
}

export function passwordPolicy() { return policy; }

/** THE question. One implementation, one call shape, no rank comparison anywhere. */
export function can(capability) {
  return !!(current && current.capabilities.has(capability));
}

/** True when the principal holds every capability in the list (a view may require several). */
export function canAll(capabilities) {
  return capabilities.every(can);
}

/** True when the principal can record a verdict — the labelling affordances key on this. */
export function canEdit() {
  return can("feedback.write") || can("label.write") || can("situation.close");
}

/** A scoped operator's picture is partial and they must be told so, on every screen. */
export function scopeSummary() {
  const scope = current && current.scope;
  if (!scope || !scope.scoped) return null;
  return {
    neCount: scope.ne_count,
    title:
      "An administrator has limited which network elements you can see. Situations may include " +
      "members outside your scope; those are shown as a redacted count. Scoping hides them from " +
      "you — it does not stop them correlating.",
  };
}
