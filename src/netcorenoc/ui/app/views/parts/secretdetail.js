/* One credential: its light, where it works, what was done with it — and the acts that need a
 * step-up (ADRs #455-#458, #471).
 *
 * * **The light** is a colour AND a word AND the sentence that earned it (`status.reason`), and it
 *   says what green does not mean: an endpoint that answers, not a password that works.
 * * **Revealing** shows the fields once, secret ones masked until asked, and hides them all again
 *   after the time the server names — a statement about the screen, never a control.
 * * **Agents** are off by default. On takes a step-up; off never does, and ends every lease.
 * * **History** is the audit log's own rows about this credential: who created it, who read it,
 *   which agent leased and used it.
 * * **An AI provider's key** shows what a device credential does not need — the models it may use,
 *   asked of the provider with the key, on request — and leaves out what it does not have: an
 *   access level, an SSH host key. **A local Ollama** lists its models as soon as it opens (no key
 *   leaves anywhere: the appliance asks the machine it already checks), with what each can do and
 *   which can work a case (ADR #477).
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { Destructive } from "../../destructive.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { StepUp, StepUpFields, stepupBody } from "./stepup.js";
import { SecretForm } from "./secretform.js";
import { KindMark, providerOf } from "./vaultui.js";
import { ModelAdvice, OllamaModels } from "./ollamamodels.js";

const LIGHT = {
  up: ["green", "Reachable"],
  degraded: ["yellow", "Intermittent"],
  down: ["red", "Unreachable"],
  unknown: ["grey", "Not checked"],
};

/** The status light: never colour alone. */
export function StatusLight({ status, compact }) {
  const [tone, word] = LIGHT[status.state] || LIGHT.unknown;
  return html`<span class=${cx("light", `light-${tone}`, compact && "light-compact")}
      title=${status.reason}>
    <span class="light-dot" aria-hidden="true"></span><span class="light-word">${word}</span>
  </span>`;
}

const ACTION = {
  "noc.secret.create": "stored it",
  "noc.secret.update": "changed it",
  "noc.secret.delete": "deleted it",
  "noc.secret.reveal": "revealed it",
  "noc.secret.read": "tried to read it",
  "noc.secret.agents": "changed agent access",
  "noc.lease.issue": "was lent a lease",
  "noc.lease.use": "used a lease",
  "noc.lease.revoke": "ended a lease",
  "noc.secret.use": "used it for",
};

/** What an AI provider's key may run, asked of the provider when the custodian wants to know —
 * at once for a local Ollama, which is asked nothing but its own list. */
class Models extends Component {
  componentDidMount() {
    const { provider } = this.props;
    if (provider && provider.own_host) this.ask();
  }

  async ask() {
    this.setState({ busy: true, error: null });
    try {
      this.setState({ busy: false, answer: await post(`/api/noc/secrets/${this.props.id}/models`) });
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ provider }, { busy, answer, error }) {
    const local = provider && provider.own_host;
    const rich = answer && answer.details && answer.details.length;
    return html`<section class="ai-models">
      <h4>${local ? "Models on this Ollama" : "Models this key may use"}</h4>
      ${rich ? html`<${ModelAdvice} answer=${answer} provider=${provider} />
          <${OllamaModels} answer=${answer} provider=${provider} />`
        : answer ? html`<p class="muted">${answer.count} model${answer.count === 1 ? "" : "s"}.</p>
          <ul class="model-chips">${answer.models.map((m) => html`<li key=${m}><code>${m}</code></li>`)}</ul>`
        : html`<button type="button" disabled=${busy} onClick=${() => this.ask()}>
          ${busy ? "Asking…" : local ? "List its models" : "Ask the provider"}</button>`}
      ${answer ? html`<button type="button" class="linkish" disabled=${busy} onClick=${() => this.ask()}>
        ${busy ? "Asking…" : "Ask again"}</button>` : null}
      ${error ? html`<p class="err" role="alert">${error}${local
        ? " Is Ollama running there, and listening beyond its own machine (OLLAMA_HOST=0.0.0.0)?" : ""}</p>` : null}
    </section>`;
  }
}

class Revealed extends Component {
  constructor(props) {
    super(props);
    this.state = { shown: {}, left: props.revealed.shown_for_s };
  }

  componentDidMount() {
    this.timer = setInterval(() => {
      const left = this.state.left - 1;
      if (left <= 0) this.props.onHide();
      else this.setState({ left });
    }, 1000);
  }

  componentWillUnmount() { clearInterval(this.timer); }

  async copy(value) {
    try { await globalThis.navigator.clipboard.writeText(value); } catch { /* selectable */ }
  }

  render({ revealed, kind, onHide }, { shown, left }) {
    return html`<div class="revealed" role="status">
      <p><b>Shown to you, and recorded.</b> Hidden again in ${left} s.${" "}
        <button type="button" class="linkish" onClick=${onHide}>Hide now</button></p>
      <dl class="revealed-fields">
        ${kind.fields.filter((f) => revealed.fields[f.name] != null).map((f) => {
          const value = revealed.fields[f.name];
          const masked = f.secret && !shown[f.name];
          return html`<div key=${f.name} class="revealed-row">
            <dt>${f.label}</dt>
            <dd><code class=${cx("revealed-value", f.multiline && "revealed-multi")}>${masked
              ? "•".repeat(12) : value}</code>
              ${f.secret ? html`<button type="button" class="linkish"
                onClick=${() => this.setState({ shown: { ...shown, [f.name]: !shown[f.name] } })}>
                ${shown[f.name] ? "Mask" : "Show"}</button>` : null}
              <button type="button" class="linkish" onClick=${() => this.copy(value)}>Copy</button>
            </dd>
          </div>`;
        })}
      </dl>
    </div>`;
  }
}

export class SecretDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { detail: null, error: null, mode: null, revealed: null, busy: false, note: null };
    this.deleting = null;
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      this.setState({ detail: await get(`/api/noc/secrets/${this.props.id}`), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  changed() {
    this.setState({ mode: null });
    this.read();
    this.props.onChanged();
  }

  async act(fn) {
    this.setState({ busy: true, note: null });
    try {
      await fn();
      this.setState({ busy: false });
      this.changed();
    } catch (error) {
      this.setState({ busy: false, note: error.message });
    }
  }

  async reveal(stepup) {
    const revealed = await post(`/api/noc/secrets/${this.props.id}/reveal`, { stepup });
    this.setState({ mode: null, revealed });
    this.read();
  }

  async forgetKey(stepup) {
    await post(`/api/noc/secrets/${this.props.id}/hostkey`, { stepup });
    this.changed();
  }

  async agentsOn(stepup) {
    await post(`/api/noc/secrets/${this.props.id}/agents`, { enabled: true, stepup });
    this.changed();
  }

  body(d) {
    const { mode, revealed } = this.state;
    const { catalogue, secondFactor } = this.props;
    const kind = catalogue.kinds.find((k) => k.id === d.kind) || { fields: [] };
    if (mode === "edit") {
      return html`<${SecretForm} catalogue=${catalogue} secret=${d} secondFactor=${secondFactor}
        onSaved=${() => this.changed()} onCancel=${() => this.setState({ mode: null })} />`;
    }
    if (mode === "reveal") {
      return html`<${StepUp} title=${`Reveal “${d.name}”`} action="Reveal"
        consequence="Who reads a credential, and when, is recorded in the audit log."
        secondFactor=${secondFactor} onConfirm=${(s) => this.reveal(s)}
        onCancel=${() => this.setState({ mode: null })} />`;
    }
    if (mode === "hostkey") {
      return html`<${StepUp} title=${`Forget the host key of “${d.name}”`} action="Forget it"
        consequence="Only after the device was replaced: the next connection pins whatever key answers."
        secondFactor=${secondFactor} onConfirm=${(s) => this.forgetKey(s)}
        onCancel=${() => this.setState({ mode: null })} />`;
    }
    if (mode === "agents") {
      return html`<${StepUp} title=${`Let agents use “${d.name}”`} action="Switch agents on"
        consequence="Agents an admin named may then ask for a short lease on it. They never see the secret."
        secondFactor=${secondFactor} onConfirm=${(s) => this.agentsOn(s)}
        onCancel=${() => this.setState({ mode: null })} />`;
    }
    return revealed ? html`<${Revealed} revealed=${revealed} kind=${kind}
      onHide=${() => this.setState({ revealed: null })} />` : null;
  }

  render({ onClose, secondFactor, catalogue }, { detail: d, error, busy, note }) {
    if (error) return html`<div class="panel-block"><p class="err" role="alert">${error}</p></div>`;
    if (!d) return html`<div class="panel-block muted">Reading the credential…</div>`;
    const writable = can("secrets.write");
    const ai = Boolean(providerOf(d.kind));
    const ssh = ["ssh_password", "ssh_key", "sftp"].includes(d.kind);
    return html`<section class="secret-detail panel-block" aria-label=${`Credential ${d.name}`}>
      <div class="pe-head">
        <h3><${KindMark} kind=${d.kind} size=${32} /> ${d.name} <${StatusLight} status=${d.status} /></h3>
        <button type="button" onClick=${onClose}>Close</button>
      </div>
      <p class="muted">${d.status.reason}${d.status.state === "up"
        ? " The endpoint answers; whether the credential is accepted is not tested yet." : ""}</p>
      <dl class="secret-facts">
        <div><dt>Type</dt><dd>${d.kind_label}${d.protocol_version ? ` · ${d.protocol_version}` : ""}</dd></div>
        <div><dt>Where</dt><dd class="mono">${d.host}:${d.port}</dd></div>
        ${ai ? null : html`<div><dt>Grants</dt><dd>${d.access_label}</dd></div>`}
        <div><dt>Version</dt><dd>${d.version}</dd></div>
        <div><dt>Stored</dt><dd title=${timeTitle(d.created_at)}>${relative(d.created_at)} by ${d.created_by}</dd></div>
        <div><dt>Last read</dt><dd title=${timeTitle(d.revealed_at)}>${d.revealed_at
          ? `${relative(d.revealed_at)} by ${d.revealed_by}` : "never"}</dd></div>
        <div><dt>Agents</dt><dd>${d.agent_access ? "may lease it" : "off"}</dd></div>
        ${ssh ? html`<div><dt>SSH host key</dt><dd title=${timeTitle(d.host_key_at)}>${d.host_key_at
          ? `pinned ${relative(d.host_key_at)}` : "not pinned yet"}${writable && d.host_key_at
          ? html` <button type="button" class="linkish"
              onClick=${() => this.setState({ mode: "hostkey" })}>Forget…</button>` : null}</dd></div>` : null}
      </dl>
      <div class="secret-acts">
        <button type="button" disabled=${busy}
          onClick=${() => this.act(() => post(`/api/noc/secrets/${d.id}/check`))}>Check now</button>
        ${can("secrets.reveal") ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ mode: "reveal", revealed: null })}>Reveal…</button>` : null}
        ${writable ? html`<button type="button" onClick=${() => this.setState({ mode: "edit" })}>Change…</button>` : null}
        ${writable && !d.agent_access ? html`<button type="button"
          onClick=${() => this.setState({ mode: "agents" })}>Let agents use it…</button>` : null}
        ${writable && d.agent_access ? html`<button type="button" class="danger" disabled=${busy}
          onClick=${() => this.act(() => post(`/api/noc/secrets/${d.id}/agents`, { enabled: false }))}>
          Switch agents off</button>` : null}
      </div>
      ${note ? html`<p class="err" role="alert">${note}</p>` : null}
      ${this.body(d)}
      ${ai ? html`<${Models} id=${d.id} provider=${(catalogue.providers || [])
        .find((p) => p.id === providerOf(d.kind))} />` : null}
      ${writable ? html`<${Destructive} compact title=${`Delete ${d.name}`} previewLabel="Delete…"
        confirmLabel=${`Delete ${d.name}`}
        consequence=${"The credential, its checks and its leases are destroyed; an agent holding a lease loses it at once. Its audit history stays."}
        preview=${async () => ({ measured: true })}
        renderPreview=${() => html`<${StepUpFields} secondFactor=${secondFactor}
          onChange=${(v) => { this.deleting = v; }} />`}
        apply=${async () => post(`/api/noc/secrets/${d.id}/delete`,
          { stepup: stepupBody(this.deleting || { username: "", password: "", otp: "" }) })}
        onDone=${() => this.props.onDeleted()} />` : null}
      <h4>Leases</h4>
      ${d.leases.length ? html`<ul class="lease-list">${d.leases.map((l) => html`<li key=${l.id}>
        <b>${l.subject_name}</b>
        <span class="muted" title=${timeTitle(l.issued_at)}>issued ${relative(l.issued_at)}</span>
        <span class="muted">${l.revoked_at ? `ended by ${l.revoked_by}`
          : l.expires_at * 1000 < Date.now() ? "expired" : `until ${relative(l.expires_at)}`}</span>
        <span class="muted">used ${l.uses}×</span>
        ${!l.revoked_at && l.expires_at * 1000 > Date.now() ? html`<button type="button" class="linkish"
          onClick=${() => this.act(() => post(`/api/noc/leases/${l.id}/revoke`))}>End</button>` : null}
      </li>`)}</ul>` : html`<p class="muted">No agent has leased it.</p>`}
      <h4>History</h4>
      <ol class="secret-history">${d.history.map((h) => html`<li key=${h.id}
          class=${cx(h.outcome !== "ok" && "history-denied")}>
        <span title=${timeTitle(h.ts)}>${relative(h.ts)}</span>
        <b>${h.actor}</b>
        <span>${ACTION[h.action] || h.action}${h.outcome === "ok" ? "" : ` — ${h.outcome}`}</span>
      </li>`)}</ol>
    </section>`;
  }
}
