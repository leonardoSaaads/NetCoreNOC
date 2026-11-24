/* Writing a routine: who it is for, when it runs, and its steps — a form, not a JSON file (ADR #492).
 *
 * Ansible's shape, in a form a lead fills in without reading the schema: **tasks** in order, each one
 * module (reach the element, run a command over SSH, read SNMP values) and one test on what came back
 * (it worked, the answer contains a word, or lacks it); **then** and **otherwise** say where to go —
 * the next task, another one, or an end: Resolve (with the cause), Wait (the device is unreachable), or
 * the NOC queue. Three starting points fill it in. The form compiles to the routine tree the server
 * validates (`engine/noc/playbook.py`: read-only commands only, every branch reaching an end); a tree
 * the form cannot draw — written by an agent or through the API — is edited as JSON instead.
 */

import { html, Component, cx } from "../../../dom.js";
import { post } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { severityTone } from "../../../widgets.js";

const SEVERITIES = ["critical", "major", "minor", "warning"];
const ACTIONS = [["ssh", "Run a command (SSH)"], ["snmp_get", "Read SNMP values"], ["reach", "Reach the element"]];
const TESTS = [["ok", "it worked"], ["contains", "the answer contains"], ["lacks", "the answer lacks"]];
const TIER_WORD = { 1: "Any agent may run it", 2: "Tier 2 agents and above", 3: "Tier 3 agents and above", 4: "Tier 4 agents only" };

const task = (over = {}) => ({ action: "ssh", arg: "", test: "ok", text: "", then: "next", else: "noc", ...over });

/** Ready-made starting points: the commands a first line runs most, in a working order. */
const TEMPLATES = [
  { key: "port", title: "Port down", icon: "signal",
    fill: { name: "Port down — check the alarm on the device", severities: ["critical", "major"], tasks: [
      task({ action: "reach", then: "next", else: "wait" }),
      task({ action: "ssh", arg: "show alarm active", test: "contains", text: "LOS", then: "noc", else: "resolve" })] } },
  { key: "reach", title: "Device unreachable", icon: "cpu",
    fill: { name: "Device unreachable — confirm before calling anyone", severities: ["critical"], tasks: [
      task({ action: "reach", then: "noc", else: "wait" })] } },
  { key: "optics", title: "Optical power", icon: "chart",
    fill: { name: "Optical power — read the transceiver", severities: ["major", "minor"], tasks: [
      task({ action: "ssh", arg: "show interfaces transceiver", test: "contains", text: "alarm", then: "noc", else: "resolve" })] } },
];

/** The form's state from a stored routine, or null when its tree is not the form's shape. */
function fromRoutine(r) {
  const doc = r.document;
  const order = [];
  let at = doc.start;
  while (at && doc.steps[at] && !doc.steps[at].end && !order.includes(at)) {
    order.push(at);
    const next = doc.steps[at].next || [];
    at = next.length ? next[next.length - 1].go : null;
    if (!next.length || next.length > 2) return null;
  }
  const where = (go) => {
    const s = doc.steps[go];
    if (!s) return null;
    if (s.end === "resolve") return "resolve";
    if (s.end === "pending") return "wait";
    if (s.end === "queue") return "noc";
    const i = order.indexOf(go);
    return i < 0 ? null : `t${i}`;
  };
  const tasks = [];
  for (const [i, name] of order.entries()) {
    const s = doc.steps[name];
    const [action, args] = s.do ? Object.entries(s.do)[0] : ["reach", {}];
    const [first, last] = [s.next[0], s.next[s.next.length - 1]];
    const cond = s.next.length === 2 ? first.if : { ok: true };
    const test = "contains" in cond ? "contains" : "not_contains" in cond ? "lacks" : "ok";
    const then = s.next.length === 2 ? where(first.go) : where(last.go);
    const otherwise = where(last.go);
    if (!then || !otherwise || ("ok" in cond && cond.ok !== true)) return null;
    const norm = (w) => (w === `t${i + 1}` ? "next" : w);
    tasks.push(task({ action, arg: action === "ssh" ? args.command : action === "snmp_get" ? args.oids.join(", ") : "",
      test, text: cond.contains || cond.not_contains || "", then: norm(then), else: norm(otherwise) }));
  }
  const end = (kind) => Object.values(doc.steps).find((s) => s.end === kind) || {};
  return { name: doc.name, description: doc.description || "", customer: r.customer_id == null ? "" : String(r.customer_id),
    tier: r.tier, vendors: (doc.match.vendors || []).join(", "), oids: (doc.match.trap_oids || []).join(", "),
    severities: doc.match.severities || [], timeout: doc.timeout_s || 300, tasks,
    cause: end("resolve").cause || "cleared_before_isolation", solution: end("resolve").solution || "",
    clear: Boolean(end("resolve").clear_alarms), status: end("pending").status || "pending_connectivity",
    told: (end("pending").update || {}).description || "" };
}

/** The form's state compiled to the routine tree the server validates. */
export function compile(st) {
  const steps = {};
  const target = (w, i) => (w === "next" ? (i + 1 < st.tasks.length ? `t${i + 1}` : "noc") : w);
  st.tasks.forEach((t, i) => {
    const args = t.action === "ssh" ? { command: t.arg.trim() }
      : t.action === "snmp_get" ? { oids: t.arg.split(/[\s,]+/).filter(Boolean) } : {};
    const cond = t.test === "contains" ? { contains: t.text.trim() } : t.test === "lacks" ? { not_contains: t.text.trim() } : { ok: true };
    steps[`t${i}`] = { do: { [t.action]: args }, next: [{ if: cond, go: target(t.then, i) }, { go: target(t.else, i) }] };
  });
  // Only the ends some step leads to: the server refuses a step nothing reaches.
  const used = new Set(Object.values(steps).flatMap((s) => s.next.map((b) => b.go)));
  const told = st.told.trim() || "The device does not answer; we are checking the path to it.";
  if (used.has("resolve")) {
    steps.resolve = { end: "resolve", cause: st.cause, solution: st.solution.trim() || "Checked on the device: no fault remains.",
      clear_alarms: st.clear };
  }
  if (used.has("wait")) {
    steps.wait = { end: "pending", status: st.status, next_update_in_s: 1800, update: { title: st.name.trim().slice(0, 120),
      description: told, executive_summary: told.slice(0, 200), current_analysis: told,
      noc_next_step: "Check again as soon as the device answers.", customer_next_step: "Nothing for now." } };
  }
  if (used.has("noc")) steps.noc = { end: "queue", note: "The routine did not conclude; a person takes it from here." };
  const list = (s) => s.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean);
  const match = {};
  if (list(st.vendors).length) match.vendors = list(st.vendors);
  if (list(st.oids).length) match.trap_oids = list(st.oids);
  if (st.severities.length) match.severities = st.severities;
  return { name: st.name.trim(), description: st.description.trim(), match, timeout_s: Number(st.timeout) || 300, start: "t0", steps };
}

const BLANK = { name: "", description: "", customer: "", tier: 1, vendors: "", oids: "", severities: ["critical"], timeout: 300,
  tasks: [task({ action: "reach", then: "next", else: "wait" })], cause: "cleared_before_isolation", solution: "", clear: false,
  status: "pending_connectivity", told: "" };

export class RoutineForm extends Component {
  constructor(props) {
    super(props);
    const r = props.routine;
    // ADR #501: a new routine from a customer's page is that customer's to begin with.
    const blank = { ...BLANK, customer: props.customerId ? String(props.customerId) : "" };
    const form = r ? fromRoutine(r) : blank;
    this.state = { st: form || { ...BLANK, name: r.name, customer: r.customer_id == null ? "" : String(r.customer_id), tier: r.tier },
      json: form ? null : JSON.stringify(r.document, null, 2), busy: false, error: null,
      note: r && r.restoring ? `Back to v${r.restoring}` : "" };
  }

  set(patch) { this.setState({ st: { ...this.state.st, ...patch }, error: null }); }

  setTask(i, patch) { this.set({ tasks: this.state.st.tasks.map((t, j) => (j === i ? { ...t, ...patch } : t)) }); }

  async save(event) {
    event.preventDefault();
    const { st, json } = this.state;
    let document;
    try { document = json != null ? JSON.parse(json) : compile(st); }
    catch { this.setState({ error: "The JSON is not valid." }); return; }
    const body = { document, customer_id: st.customer ? Number(st.customer) : null, tier: Number(st.tier),
      note: this.state.note.trim() };
    this.setState({ busy: true, error: null });
    try {
      const r = this.props.routine;
      const view = await post(r ? `/api/noc/playbooks/${r.id}` : "/api/noc/playbooks", body);
      this.props.onSaved(view);
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  render({ routine, labels, onCancel }, { st, json, busy, error }) {
    const goto = (i) => [["next", i + 1 < st.tasks.length ? `Task ${i + 2}` : "NOC queue"],
      ...st.tasks.map((_, j) => [`t${j}`, `Task ${j + 1}`]).filter(([k]) => k !== `t${i}` && k !== `t${i + 1}`),
      ["resolve", "Resolve"], ["wait", "Wait — unreachable"], ["noc", "NOC queue"]];
    return html`<form class="rt-form" onSubmit=${(e) => this.save(e)} autocomplete="off">
      <header class="rt-form-head"><h2>${routine ? `Edit “${routine.name}”${routine.restoring ? ` from v${routine.restoring}` : ""}` : "New routine"}</h2>
        <button type="button" class="btn-ghost" onClick=${onCancel}><${Icon} name="cross" /> Cancel</button></header>

      ${routine ? null : html`<div class="rt-templates" aria-label="Start from">${TEMPLATES.map((t) => html`<button key=${t.key}
          type="button" class="rt-template" onClick=${() => this.setState({ st: { ...BLANK, ...t.fill }, json: null })}>
          <${Icon} name=${t.icon} /><b>${t.title}</b></button>`)}</div>`}

      <section class="panel-block rt-sec"><h3><span class="rt-n">1</span> Who it is for</h3>
        <div class="rt-grid">
          <label class="pe-field"><span>Name</span><input required maxlength="80" value=${st.name}
            placeholder="Port down on a Ciena edge — check before calling" onInput=${(e) => this.set({ name: e.currentTarget.value })} /></label>
          <label class="pe-field"><span>Customer</span><select value=${st.customer} onChange=${(e) => this.set({ customer: e.currentTarget.value })}>
            <option value="">Every customer</option>
            ${labels.customers.map((c) => html`<option key=${c.id} value=${String(c.id)}>${c.name}</option>`)}</select></label>
        </div>
        <div class="pe-field"><span>Tier — who may run it</span>
          <div class="seg" role="radiogroup" aria-label="Tier">${labels.tiers.map((t) => html`<button key=${t} type="button"
            role="radio" aria-checked=${Number(st.tier) === t} class=${cx("seg-btn", Number(st.tier) === t && "on")}
            onClick=${() => this.set({ tier: t })}>Tier ${t}</button>`)}</div>
          <small class="muted">${TIER_WORD[st.tier]}. A lower tier never sees it.</small></div>
      </section>

      ${json != null ? html`<section class="panel-block rt-sec"><h3><span class="rt-n">2</span> The routine, as JSON</h3>
          <p class="muted">This routine's tree has branches the form cannot draw.</p>
          <textarea class="rt-json mono" rows="18" value=${json} onInput=${(e) => this.setState({ json: e.currentTarget.value })}></textarea>
        </section>` : html`
      <section class="panel-block rt-sec"><h3><span class="rt-n">2</span> When it runs</h3>
        <div class="rt-grid">
          <label class="pe-field"><span>Vendors</span><input value=${st.vendors} placeholder="ciena, huawei"
            onInput=${(e) => this.set({ vendors: e.currentTarget.value })} /></label>
          <label class="pe-field"><span>Trap OIDs (a prefix matches its subtree)</span><input value=${st.oids} placeholder="1.3.6.1.4.1.1271"
            onInput=${(e) => this.set({ oids: e.currentTarget.value })} /></label>
        </div>
        <div class="rt-sevs">${SEVERITIES.map((s) => html`<label key=${s} class=${cx("rt-chip", st.severities.includes(s) && "on")}>
          <span class=${cx("rt-dot", severityTone(s))} aria-hidden="true"></span><input type="checkbox" checked=${st.severities.includes(s)} onChange=${(e) => this.set({ severities: e.currentTarget.checked
            ? [...st.severities, s] : st.severities.filter((x) => x !== s) })} /> ${s}</label>`)}</div>
      </section>

      <section class="panel-block rt-sec"><h3><span class="rt-n">3</span> Steps</h3>
        <ol class="rt-tasks">${st.tasks.map((t, i) => html`<li key=${i} class="rt-task">
          <div class="rt-task-head"><span class="rt-task-n">${i + 1}</span>
            <select aria-label="What it does" value=${t.action} onChange=${(e) => this.setTask(i, { action: e.currentTarget.value })}>
              ${ACTIONS.map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
            ${t.action === "reach" ? html`<span class="muted">ping and the management ports</span>` : html`<input class="mono" required
              aria-label=${t.action === "ssh" ? "Command" : "OIDs"} value=${t.arg}
              placeholder=${t.action === "ssh" ? "show alarm active" : "1.3.6.1.2.1.2.2.1.8.{entity}"}
              onInput=${(e) => this.setTask(i, { arg: e.currentTarget.value })} />`}
            ${st.tasks.length > 1 ? html`<button type="button" class="btn-ghost" aria-label=${`Remove step ${i + 1}`}
              onClick=${() => this.set({ tasks: st.tasks.filter((_, j) => j !== i) })}><${Icon} name="cross" /></button>` : null}
          </div>
          <div class="rt-task-rule"><span>If</span>
            <select aria-label="Test" value=${t.test} onChange=${(e) => this.setTask(i, { test: e.currentTarget.value })}>
              ${TESTS.map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
            ${t.test === "ok" ? null : html`<input required aria-label="Text" value=${t.text} placeholder="LOS"
              onInput=${(e) => this.setTask(i, { text: e.currentTarget.value })} />`}
            <span>then</span><select aria-label="Then" value=${t.then} onChange=${(e) => this.setTask(i, { then: e.currentTarget.value })}>
              ${goto(i).map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
            <span>otherwise</span><select aria-label="Otherwise" value=${t.else} onChange=${(e) => this.setTask(i, { else: e.currentTarget.value })}>
              ${goto(i).map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
          </div>
        </li>`)}</ol>
        <button type="button" class="tap" onClick=${() => this.set({ tasks: [...st.tasks, task()] })}><${Icon} name="playbook" /> Add a step</button>
        <p class="hint">Read-only commands only: the appliance refuses any that changes the device.
          ${" "}<code>{ip}</code> and <code>{entity}</code> stand for the alarm's element and port.</p>
      </section>

      <section class="panel-block rt-sec"><h3><span class="rt-n">4</span> How it ends</h3>
        <div class="rt-ends">
          <div class="rt-end rt-end-resolve"><b><${Icon} name="check" /> Resolve</b>
            <select aria-label="Cause" value=${st.cause} onChange=${(e) => this.set({ cause: e.currentTarget.value })}>
              ${Object.entries(labels.causes).map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
            <input aria-label="Solution" value=${st.solution} placeholder="Not active on the device; the clear trap never arrived."
              onInput=${(e) => this.set({ solution: e.currentTarget.value })} />
            <label class="check-row"><input type="checkbox" checked=${st.clear} onChange=${(e) => this.set({ clear: e.currentTarget.checked })} />
              Clear the alarms</label></div>
          <div class="rt-end rt-end-wait"><b><${Icon} name="pause" /> Wait</b>
            <select aria-label="Waiting on" value=${st.status} onChange=${(e) => this.set({ status: e.currentTarget.value })}>
              ${Object.entries(labels.pending).map(([k, w]) => html`<option key=${k} value=${k}>${w}</option>`)}</select>
            <input aria-label="What the customer is told" value=${st.told} placeholder="The device does not answer; we are checking the path to it."
              onInput=${(e) => this.set({ told: e.currentTarget.value })} /></div>
          <div class="rt-end rt-end-noc"><b><${Icon} name="headset" /> NOC queue</b>
            <span class="muted">A person takes it from there.</span></div>
        </div>
      </section>`}

      ${routine ? html`<label class="pe-field rt-note"><span>What changed, and why</span>
        <input maxlength="300" value=${this.state.note} placeholder="Ping before SSH: the last case was a dead path, not a dead port"
          onInput=${(e) => this.setState({ note: e.currentTarget.value })} />
        <small class="muted">Kept with this version, so the next reader knows what it learned (and from which case).</small></label>` : null}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <footer class="acct-actions">
        <button type="button" onClick=${onCancel}>Cancel</button>
        <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : routine ? "Save routine" : "Create routine"}</button>
      </footer>
    </form>`;
  }
}
