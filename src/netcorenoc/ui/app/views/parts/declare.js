/* The operator's declaration: what this equipment is, what this trap means, how serious it is.
 *
 * ## Why this exists, and why it is here rather than on three screens
 *
 * The appliance starts knowing nothing about the customer's network and becomes intelligent from
 * the trap stream itself. Three complaints came from three different screens — *"I renamed the host
 * and nothing changed in Entities"*, *"what is Alarm Classes for if it changes nothing?"*, *"where
 * is the severity?"* — and they are one gap: there was nowhere for an operator to write down what
 * they already knew. Three declarations, one missing place.
 *
 * The place is the row in a situation's member table, because that is where the operator already
 * is when the trap arrives. It propagates to Entities, to the Network Graph and to Alarm Classes
 * because all of them read the same `label` table (DECISIONS #281, #283).
 *
 * ## Three rules, and the same three for all of them
 *
 *   1. **The declared wins; the derived is kept.** The server never overwrites what it learned —
 *      precedence is decided at read time — so a disagreement between an operator and the
 *      appliance survives as evidence instead of being spent (DECISIONS #284).
 *   2. **Every declaration can be withdrawn.** A declaration that cannot be undone is a
 *      declaration nobody makes, so each control offers `Clear` whenever one is in force, and the
 *      derived value is still underneath it.
 *   3. **A declaration asserts nothing about a grouping.** A name is not a claim about which
 *      alarms belong together, and a severity is a claim about a kind of trap rather than about a
 *      link, so none of this produces a training row and
 *      `PREREGISTRATION-0.16.0.md` §2's map is unamended (DECISIONS #286).
 *
 * These are NOT the five gestures. There is no confidence control, for the same reason the zombie
 * clear has none: there is no number for an operator to give, because nothing learns from it.
 */

import { html, Component } from "../../dom.js";
import { post, del } from "../../api.js";
import { severity as severityOf } from "../../format.js";

/** The five tokens `known_oids.SEVERITY_VOCAB` holds. The route refuses anything else. */
export const SEVERITY_TOKENS = ["critical", "major", "minor", "warning", "indeterminate"];

/**
 * **The interruption rule, in one function** (DECISIONS #285).
 *
 * A confirmation appears only when the appliance has a **confirmed** severity for this alarm — it
 * passed both of `severity.py`'s gates, shape and ordinality, which is what writing
 * `alarm.severity` at all means — and the declared rank is **two or more steps** from the learned
 * one on the 0–4 vocabulary scale.
 *
 * **It must be rare or it will be dismissed.** It is rare by construction: on a ten-scenario
 * corpus replay, 0 of 2 252 alarms resolve a severity at all, so nothing there can reach this. When
 * it does fire, the appliance has 200 observations and 50 closed alarms saying the opposite of the
 * operator, and that is worth interrupting for.
 *
 * **An out-of-scale learned rank never prompts.** F99's integer scale — a vendor numbering severity
 * 10, 20, 30 — is not the declaration's scale, so `|declared − learned|` would be comparing two
 * different rulers. The appliance holds no *comparable* opinion there and has nothing to interrupt
 * with.
 */
export function disagrees(alarm, declaredRank) {
  const learned = alarm.severity_rank;
  if (alarm.severity == null || typeof learned !== "number") return false;
  if (learned < 0 || learned > 4) return false; // not the vocabulary's scale (F99)
  return Math.abs(declaredRank - learned) >= 2;
}

/**
 * One declaration control: a button that opens the editor, the editor, and — when the caller says
 * a value contradicts the appliance — an inline confirmation between them.
 *
 * **The confirmation is an element on the page, not `globalThis.confirm`.** A native dialog is
 * unstyled, blocks the event loop, cannot be reached by the DOM harness, and is the one control in
 * a console that no test in this repository can see — which is the shape of defect eight
 * consecutive releases have shipped. `tests/test_security_ui.py` refuses a bare `confirm(` for
 * exactly that reason, and it was right to.
 *
 * It is deliberately **not** a `Destructive`. That component says *"this cannot be undone"* and
 * requires a preview, and neither is true here: nothing is destroyed, the appliance's own value is
 * kept underneath, and `Clear` puts it back. Wrapping a reversible action in the irreversible
 * component would make the word "destructive" mean less everywhere it is used.
 */
class Declaration extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: false, value: props.value || "", busy: false, error: null, warn: null };
  }

  async run(work) {
    if (this.state.busy) return;
    this.setState({ busy: true, error: null });
    try {
      await work();
      this.setState({ busy: false, editing: false, warn: null });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  /** Save, or raise the confirmation first. Cancelling it **does not write** (DECISIONS #285). */
  submit() {
    const { contradicts, onSave } = this.props;
    const warn = contradicts ? contradicts(this.state.value) : null;
    if (warn && !this.state.warn) {
      this.setState({ warn });
      return;
    }
    this.run(() => onSave(this.state.value));
  }

  render({ label, value, children, onClear }, state) {
    const { editing, busy, error, warn } = state;
    if (!editing) {
      // **No server string reaches an attribute here**, and that is the same rule the member
      // row's `aria-label` follows for the same reason: a declaration is attacker-influenced text,
      // and putting it in a `title=` means a screen reader announces whatever arrived in a trap.
      // It is inert as markup either way; "inert" is not the same as "appropriate to read aloud".
      // The declared value is already rendered, in the cell beside this button.
      return html`<button type="button" class="tap declare-open"
        title=${value
          ? "A declaration is in force. Editing it replaces it; Clear withdraws it and the " +
            "appliance's own value comes back."
          : `Declare ${label}. It takes precedence, and nothing the appliance learned is ` +
            "overwritten."}
        onClick=${() => this.setState({ editing: true, value: value || "", warn: null })}
      >${value ? "Edit" : "Declare"}</button>`;
    }
    return html`<form class="inline-form declare"
      onSubmit=${(e) => { e.preventDefault(); this.submit(); }}>
      ${children({ value: state.value,
                   set: (v) => this.setState({ value: v, warn: null }) })}
      <button type="submit" class="primary" disabled=${busy || !String(state.value).trim()}
      >${warn ? "Declare anyway" : "Save"}</button>
      ${value ? html`<button type="button" class="tap"
        title="Withdraw this declaration. What the appliance derived is still underneath it."
        onClick=${() => this.run(onClear)}>Clear</button>` : null}
      <button type="button" class="tap"
        onClick=${() => this.setState({ editing: false, error: null, warn: null })}>Cancel</button>
      ${warn ? html`<div class="warnbox declare-warn" role="alert">${warn}</div>` : null}
      ${error ? html`<span class="err" role="alert">${error.detail || error.message}</span>` : null}
    </form>`;
  }
}

/** Name the network element this alarm came from. Propagates to Entities and to the graph. */
export function DeclareNe({ alarm, onDone }) {
  return html`<${Declaration} label="a name for this element" value=${alarm.device_label}
    onDone=${onDone}
    onSave=${(v) => post("/api/labels", { kind: "ne", id: alarm.ne_id, label: v.trim() })}
    onClear=${() => del(`/api/labels/ne/${alarm.ne_id}`)}>
    ${({ value, set }) => html`<label class="visually-hidden" for=${`ne-${alarm.id}`}
        >Name for this network element</label>
      <input id=${`ne-${alarm.id}`} value=${value} maxlength="120" autocomplete="off"
             onInput=${(e) => set(e.target.value)} />`}
  <//>`;
}

/** Name the kind of trap. Propagates to Alarm Classes, the timeline and every situation card. */
export function DeclareClass({ alarm, onDone }) {
  return html`<${Declaration} label="a name for this kind of trap" value=${alarm.class_label}
    onDone=${onDone}
    onSave=${(v) => post("/api/labels", { kind: "class", id: alarm.class_id, label: v.trim() })}
    onClear=${() => del(`/api/labels/class/${alarm.class_id}`)}>
    ${({ value, set }) => html`<label class="visually-hidden" for=${`cls-${alarm.id}`}
        >Name for this alarm class</label>
      <input id=${`cls-${alarm.id}`} value=${value} maxlength="120" autocomplete="off"
             onInput=${(e) => set(e.target.value)} />`}
  <//>`;
}

/**
 * Declare how serious this kind of trap is.
 *
 * A `<select>` and not a text field: the five tokens are the vocabulary the appliance renders, the
 * route refuses anything else, and a free-text severity would be the fabricated value
 * `severity.py` exists to refuse, arriving through the front door.
 *
 * **Per alarm class**, which is what the control says — a severity is a property of a kind of trap,
 * not of one alarm. `qualifier=''` carries that meaning in the schema, and the column is already
 * wide enough for a later class + varbind refinement (DECISIONS #283).
 */
export function DeclareSeverity({ alarm, onDone }) {
  // **The interruption, and nothing else.** It changes what the operator sees and never what is
  // written: `Cancel` closes the form without a request, `Declare anyway` sends the same POST it
  // would have sent. The disagreement is recorded server-side on every declaration that lands,
  // so the record does not depend on this dialog having appeared (DECISIONS #285).
  const warn = (token) => {
    const rank = SEVERITY_TOKENS.indexOf(token);
    if (!disagrees(alarm, rank)) return null;
    const learned = severityOf({ severity: alarm.severity, severity_rank: alarm.severity_rank });
    return `The appliance learned "${learned.text}" here, from at least 200 observations and ` +
      `50 closed alarms whose lifetimes confirmed the ordering. You are declaring "${token}", ` +
      `${Math.abs(rank - alarm.severity_rank)} steps away. What it learned is kept either way.`;
  };
  return html`<${Declaration} label="the severity of this kind of trap"
    value=${alarm.declared_severity} onDone=${onDone} contradicts=${warn}
    onSave=${(token) => post("/api/labels",
                             { kind: "severity", id: alarm.class_id, label: token })}
    onClear=${() => del(`/api/labels/severity/${alarm.class_id}`)}>
    ${({ value, set }) => html`<label class="visually-hidden" for=${`declared-severity-${alarm.id}`}
        >Severity for this alarm class</label>
      <select id=${`declared-severity-${alarm.id}`} value=${value || "critical"}
              onChange=${(e) => set(e.target.value)}>
        ${SEVERITY_TOKENS.map((t) => html`<option key=${t} value=${t}
          selected=${(value || "critical") === t}>${t}</option>`)}
      </select>`}
  <//>`;
}
