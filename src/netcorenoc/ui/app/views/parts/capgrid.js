/* The capability grid: every capability, in a column per category, one checkbox each (v0.25.0,
 * ADR #403).
 *
 * A capability is shown by what it lets a person do ("Clear alarms"), with its identifier in the
 * tooltip. Three states a box can be in, and each says why:
 *
 *   * **locked off** — above the role's ceiling. The appliance cannot grant it to this role at all
 *     (a policy only narrows), so the box is disabled and names the role that holds it;
 *   * **off for the whole role** — the role's baseline (the Roles tab) removed it, so no one person
 *     of that role can have it back without changing the baseline;
 *   * **kept** — an admin's recovery set (`rbac.*`, `scope.*`, `self.read`), always held, so a
 *     policy can never lock the last admin out of repairing it.
 *
 * Everything else is a plain toggle, and a group's header toggles the whole group. The component
 * holds no state: `value` in, `onChange(Set)` out.
 */

import { html, cx } from "../../dom.js";

/** Category -> [capability, what it lets a person do]. Unknown capabilities go to "Other". */
export const CATEGORIES = [
  ["Situations & alarms", [
    ["situations.read", "View situations"], ["situation.close", "Close"],
    ["situation.move", "Move alarms"], ["situation.merge", "Merge"],
    ["situation.split", "Split"], ["situation.promote", "Promote"],
    ["alarm.acknowledge", "Acknowledge alarms"], ["alarm.clear", "Clear alarms"],
    ["feedback.write", "Confirm groupings"], ["label.write", "Name and grade"],
  ]],
  ["Monitoring", [
    ["stats.read", "Overview"], ["graph.read", "Network graph"],
    ["timeline.read", "Timeline"], ["events.stream", "Live updates"],
    ["entities.read", "Entities"], ["classes.read", "Trap catalogue"],
    ["correlation.read", "Correlation health"], ["notice.snooze", "Snooze warnings"],
  ]],
  ["Catalogue & learning", [
    ["catalogue.write", "Edit trap rules"], ["catalogue.import", "Import trap lists"],
    ["entity.reset", "Reset identity"], ["profile.reset", "Wipe evidence"],
    ["model.read", "View models"], ["model.register", "Register models"],
    ["promotion.read", "View promotion"], ["promotion.write", "Promote models"],
    ["scorer.read", "View link scorer"], ["scorer.preview", "Preview scorer"],
    ["scorer.write", "Change scorer"],
  ]],
  ["Maintenance", [
    ["mw.read", "View windows"], ["mw.write", "Plan windows"], ["mw.confirm", "Confirm windows"],
    ["mw.ledger", "Window ledger"], ["organizations.read", "View organizations"],
    ["organizations.write", "Edit organizations"], ["timezones.read", "Time zones"],
  ]],
  ["Administration", [
    ["users.manage", "Manage users"], ["tokens.manage", "Manage tokens"],
    ["rbac.read", "View access"], ["rbac.write", "Change access"],
    ["scope.read", "View visibility"], ["scope.write", "Change visibility"],
    ["config.read", "View settings"], ["config.write", "Change settings"],
    ["audit.read", "Audit log"], ["audit.export", "Export audit"], ["audit.prune", "Prune audit"],
    ["quarantine.read", "Quarantine"], ["self.read", "Own account"],
  ]],
];

const ROLE_WORD = { viewer: "Viewer", editor: "Editor", admin: "Admin" };

/** The categories, with any capability the server knows and this list does not under "Other". */
export function categories(all) {
  const known = new Set(CATEGORIES.flatMap(([, caps]) => caps.map(([id]) => id)));
  const extra = (all || []).filter((id) => !known.has(id)).map((id) => [id, id]);
  const out = CATEGORIES.map(([name, caps]) => [name, caps.filter(([id]) => !all || all.includes(id))]);
  return extra.length ? [...out, ["Other", extra]] : out;
}

/**
 * The recovery set a role always keeps: held only by the role whose ceiling contains all of it.
 * Derived from what the server reports, never from comparing role names (F-rank).
 */
export function keptBy(rbac, ceiling) {
  const recovery = rbac.recovery_capabilities || [];
  return recovery.every((c) => ceiling.has(c)) ? new Set(recovery) : new Set();
}

/** Whether two sets hold the same members. */
export function sameSet(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

/**
 * `all`: every capability id. `ceiling`: what the role may ever hold. `baseline`: what the role holds
 * after its baseline (for a person) — for a role's own grid, the ceiling. `locked`: always held.
 * `minimum`: `{capability: role}`. `value`: the Set shown. `readOnly` draws no toggles.
 */
export function CapGrid({ all, ceiling, baseline, locked, minimum, value, onChange, readOnly,
  roleName }) {
  const state = (id) => {
    if (locked && locked.has(id)) return "locked";
    if (!ceiling.has(id)) return "above";
    if (baseline && !baseline.has(id)) return "role-off";
    return "open";
  };
  const set = (ids, on) => {
    const next = new Set(value);
    for (const id of ids) {
      if (state(id) !== "open") continue;
      if (on) next.add(id); else next.delete(id);
    }
    onChange(next);
  };
  return html`<div class="capgrid" role="group" aria-label="Capabilities">
    ${categories(all).map(([name, caps]) => {
      const open = caps.filter(([id]) => state(id) === "open").map(([id]) => id);
      const held = caps.filter(([id]) => value.has(id)).length;
      const allOn = open.length > 0 && open.every((id) => value.has(id));
      return html`<fieldset key=${name} class="capgroup">
        <legend>
          ${readOnly || !open.length ? html`<span class="capgroup-name">${name}</span>` : html`
            <label class="capgroup-name"><input type="checkbox" checked=${allOn}
              indeterminate=${!allOn && open.some((id) => value.has(id))}
              onChange=${(e) => set(open, e.currentTarget.checked)} />${name}</label>`}
          <span class="capgroup-n">${held}/${caps.length}</span>
        </legend>
        ${caps.map(([id, label]) => {
          const why = state(id);
          const title = why === "above"
            ? `${id} — needs the ${ROLE_WORD[minimum[id]] || minimum[id]} role`
            : why === "role-off" ? `${id} — off for every ${roleName || "member of this role"} (Roles tab)`
              : why === "locked" ? `${id} — always kept, so access can always be repaired` : id;
          return html`<label key=${id} class=${cx("capitem", `cap-${why}`)} title=${title}>
            <input type="checkbox" checked=${value.has(id)} disabled=${readOnly || why !== "open"}
              onChange=${(e) => set([id], e.currentTarget.checked)} />
            <span>${label}</span>
            ${why === "above" ? html`<span class="caplock" aria-hidden="true">${ROLE_WORD[minimum[id]] || ""}</span>` : null}
          </label>`;
        })}
      </fieldset>`;
    })}
  </div>`;
}
