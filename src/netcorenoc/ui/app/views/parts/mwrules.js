/* Card 3: **what still gets through** (D4, IV.1).
 *
 * Per target, the default is **nothing**. A rule is added as a chip, and the maintainer's own
 * example has to be expressible in a few taps:
 *
 *   On host A, collect only critical alarms.
 *   On host B, collect traps only from 11:15 to 11:20, and only OIDs under
 *   1.3.6.1.4.1.2011.5.25.31.1.1.1.1.
 *
 * Host A is: pick the host, tap *Severity*, tap *Critical*. Three taps. Host B is: pick the host,
 * tap *Time slot*, two times, tap *OID subtree*, paste the arc. The composition — different kinds
 * AND, same kind OR — is stated **once**, on the target, in the one sentence that changes what the
 * operator does, and is not repeated per chip.
 *
 * ## The two explanatory lines in this file, and why they are the only two
 *
 * *"Collecting nothing"* on a target with no rules, because an absent rule is a decision and an
 * operator has to be able to see that they made it. And the AND/OR sentence, because it is the one
 * thing about this screen that cannot be inferred from the controls. Everything else — that a
 * severity is a band, that a slot is two times — is the control saying it.
 */

import { html, Component } from "../../dom.js";
import { band } from "../../format.js";

/* The three kinds, in the order an operator reaches for them. Severity first because it is the
 * commonest and the cheapest to express; the OID subtree last because it is the one they paste. */
const KINDS = [
  { kind: "severity", label: "Severity" },
  { kind: "slot", label: "Time slot" },
  { kind: "oid", label: "OID subtree" },
];

/* X.733's ranks, named. `format.band` owns the naming everywhere else and this reads it, so the
 * chip and the alarm pill cannot disagree about what rank 1 is called. */
const RANKS = [0, 1, 2, 3, 4];

export function describeRule(rule) {
  if (rule.kind === "severity") {
    return `${band(rule.severity_rank).label} and above`;
  }
  if (rule.kind === "slot") {
    return `${(rule.slot_local_from || "").slice(11, 16)}–${(rule.slot_local_to || "").slice(11, 16)}`;
  }
  return `${rule.match_on === "trap" ? "trap" : "varbind"} OID under ${rule.oid_root}`;
}

/* One target's rules, with the controls that add one. */
export class TargetRules extends Component {
  constructor(props) {
    super(props);
    this.state = { adding: null, draft: {} };
  }

  add() {
    const { adding, draft } = this.state;
    if (!adding) return;
    const rule = { kind: adding, ne_id: this.props.target.ne_id, ...draft };
    if (adding === "severity" && rule.severity_rank === undefined) rule.severity_rank = 0;
    if (adding === "oid" && !rule.oid_root) return;
    if (adding === "oid" && !rule.match_on) rule.match_on = "varbind";
    if (adding === "slot" && !(rule.slot_local_from && rule.slot_local_to)) return;
    this.props.onChange([...this.props.rules, rule]);
    this.setState({ adding: null, draft: {} });
  }

  view() {
    const { target, rules, onChange } = this.props;
    const { adding, draft } = this.state;
    const mine = rules.filter((r) => r.ne_id === target.ne_id);
    return html`<li class="mw-target" data-ne=${target.ne_id}>
      <div class="mw-target-head">
        <strong>${target.label || target.address}</strong>
        ${target.label ? html`<span class="muted mono">${target.address}</span>` : null}
      </div>
      <div class="mw-chips" data-role="chips">
        ${mine.length === 0
          ? html`<span class="mw-chip mw-chip-none" data-role="collects-nothing"
              >Collecting nothing</span
            >`
          : mine.map(
              (rule, i) => html`<span class="mw-chip" key=${i} data-kind=${rule.kind}>
                ${describeRule(rule)}
                <button
                  type="button"
                  class="mw-chip-x"
                  aria-label=${`Remove: ${describeRule(rule)}`}
                  onClick=${() => onChange(rules.filter((r) => r !== rule))}
                >
                  ×
                </button>
              </span>`,
            )}
      </div>
      ${mine.length > 1
        ? html`<p class="hint mw-compose">
            All of these must be true, and where two are the same kind either one will do.
          </p>`
        : null}
      <div class="mw-add">
        ${KINDS.map(
          (k) => html`<button
            type="button"
            key=${k.kind}
            class=${adding === k.kind ? "on" : ""}
            data-add=${k.kind}
            onClick=${() =>
              this.setState({ adding: adding === k.kind ? null : k.kind, draft: {} })}
          >
            ${k.label}
          </button>`,
        )}
      </div>
      ${adding === "severity"
        ? html`<div class="mw-draft" data-draft="severity">
            ${RANKS.map(
              (rank) => html`<button
                type="button"
                key=${rank}
                class=${draft.severity_rank === rank ? "on" : ""}
                onClick=${() => this.setState({ draft: { severity_rank: rank } }, () => this.add())}
              >
                ${band(rank).label}
              </button>`,
            )}
          </div>`
        : null}
      ${adding === "slot"
        ? html`<div class="mw-draft" data-draft="slot">
            <label
              >From
              <input
                type="datetime-local"
                onInput=${(e) =>
                  this.setState({ draft: { ...draft, slot_local_from: e.target.value } })}
            /></label>
            <label
              >To
              <input
                type="datetime-local"
                onInput=${(e) =>
                  this.setState({ draft: { ...draft, slot_local_to: e.target.value } })}
            /></label>
            <button type="button" onClick=${() => this.add()}>Add</button>
          </div>`
        : null}
      ${adding === "oid"
        ? html`<div class="mw-draft" data-draft="oid">
            <input
              type="text"
              inputmode="numeric"
              placeholder="1.3.6.1.4.1.2011.5.25.31.1.1.1.1"
              onInput=${(e) => this.setState({ draft: { ...draft, oid_root: e.target.value } })}
            />
            <div class="mw-match">
              ${["varbind", "trap"].map(
                (where) => html`<button
                  type="button"
                  key=${where}
                  class=${(draft.match_on || "varbind") === where ? "on" : ""}
                  onClick=${() => this.setState({ draft: { ...draft, match_on: where } })}
                >
                  ${where === "varbind" ? "in a varbind" : "the trap OID"}
                </button>`,
              )}
            </div>
            <button type="button" onClick=${() => this.add()}>Add</button>
          </div>`
        : null}
    </li>`;
  }

  render() { return this.view(); }
}

/* Card 3 itself: every target, each with its rules. */
export function RulesCard({ targets, rules, onChange }) {
  if (targets.length === 0) {
    return html`<p class="hint">Choose the hosts first.</p>`;
  }
  return html`<ul class="mw-targets" data-role="rules">
    ${targets.map(
      (target) => html`<${TargetRules}
        key=${target.ne_id}
        target=${target}
        rules=${rules}
        onChange=${onChange}
      />`,
    )}
  </ul>`;
}
