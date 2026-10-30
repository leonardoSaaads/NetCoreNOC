/* NOC → Secrets → AI providers: the keys agents think with (ADR #471).
 *
 * One card per provider — Anthropic (Claude), OpenAI, Google AI Studio (Gemini), xAI (Grok), Meta
 * (Llama), Ollama and Ollama Cloud — with its logo, whether it is connected, and one button.
 * Connecting is three things on one panel: a name (already filled), the key pasted (with how its
 * keys begin, and where to get one), and whether agents may run on it. **A local Ollama asks for no
 * key** (ADR #477): it asks where it runs, and once stored its credential opens on the models
 * pulled on that machine — which can call tools, and so can work a case. Only a proxy in front of
 * it may want a token, and that field stays folded until someone says so. Ollama Cloud is a cloud
 * provider like the others: ollama.com, with the account's key.
 *
 * What happens to the key is the vault's (ADRs #455-#458): sealed twice, bound to the provider's
 * own API — a cloud provider's key cannot be pointed anywhere else — read back only by a custodian
 * after a step-up, and **never given to an agent**: the appliance calls the provider for it, and
 * every such call is an audit row under the agent's name. Letting agents run on a key is a step-up
 * of its own, asked right after storing it.
 */

import { html, Component, cx } from "../../dom.js";
import { post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { BrandMark } from "../../brands.js";
import { PasswordInput } from "../../password.js";
import { StepUp } from "./stepup.js";
import { StatusLight } from "./secretdetail.js";
import { providerOf } from "./vaultui.js";

let seq = 0;

function draft(provider) {
  return { name: provider.label, key: "", host: provider.host, port: String(provider.port),
    scheme: provider.scheme, agents: true, proxy: false };
}

/** The key field: a cloud provider's key, or a local Ollama's proxy token when one is asked. */
function KeyField({ p, provider, st, set }) {
  return html`<div class="pe-field ai-key-field">
    <${PasswordInput} id=${`${p}-key`} label=${provider.key_label} value=${st.key}
      autocomplete="new-password" onInput=${(key) => set({ key })} />
    ${provider.key_hint ? html`<p class="hint">${provider.label} keys begin with
      ${" "}<code>${provider.key_hint}</code>${st.key && !st.key.startsWith(provider.key_hint)
        ? html` — <span class="warn-text">this one does not; check it is the right key.</span>` : null}</p>` : null}
  </div>`;
}

/** The panel that connects one provider: name, key (or where Ollama runs), agents. */
class Connect extends Component {
  constructor(props) {
    super(props);
    seq += 1;
    this.p = `aik-${seq}`;
    this.state = { ...draft(props.provider), busy: false, error: null, stored: null };
  }

  async submit(event) {
    event.preventDefault();
    const { provider } = this.props;
    const st = this.state;
    this.setState({ busy: true, error: null });
    try {
      const saved = await post("/api/noc/secrets", {
        name: st.name.trim(), description: "", kind: provider.kind, access_level: "view_only",
        host: provider.own_host ? st.host.trim() : provider.host,
        port: provider.own_host ? Number(st.port) || provider.port : provider.port,
        protocol_version: provider.own_host ? st.scheme : provider.scheme, monitor: true,
        fields: st.key ? { api_key: st.key } : {},
      });
      this.setState({ busy: false, key: "", stored: saved });
      if (!st.agents) this.props.onDone(saved, false);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  async agentsOn(stepup) {
    const { stored } = this.state;
    await post(`/api/noc/secrets/${stored.id}/agents`, { enabled: true, stepup });
    this.props.onDone(stored, true);
  }

  render({ provider, secondFactor, onCancel }, st) {
    const p = this.p;
    if (st.stored) {
      return html`<${StepUp} title=${`Let agents run on “${st.stored.name}”`} action="Let agents use it"
        consequence=${`The appliance then calls ${provider.label} with this key for the agents an admin hires on it. No agent is ever shown the key.`}
        secondFactor=${secondFactor} onConfirm=${(s) => this.agentsOn(s)}
        onCancel=${() => this.props.onDone(st.stored, false)} />`;
    }
    const set = (patch) => this.setState(patch);
    return html`<form class="ai-connect panel-block" onSubmit=${(e) => this.submit(e)} autocomplete="off"
        aria-labelledby=${`${p}-h`}>
      <div class="sf-head">
        <${BrandMark} brand=${provider.id} size=${44} />
        <div><h3 id=${`${p}-h`}>Connect ${provider.label}</h3>
          <p class="muted">${provider.own_host
            ? "Models running on your own machine or a GPU server on your network. No key: Ollama runs them there."
            : `${provider.product} through ${provider.label}'s API at ${provider.host}.`}</p></div>
        <a class="ai-getkey" href=${provider.key_url} target="_blank" rel="noopener noreferrer">
          ${provider.own_host ? "Install Ollama" : "Get a key"} <${Icon} name="external" /></a>
      </div>
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-name`}>Name</label>
          <input id=${`${p}-name`} value=${st.name} required maxlength="80"
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></div>
        ${provider.own_host ? html`
          <div class="pe-field"><label for=${`${p}-host`}>Where it runs</label>
            <input id=${`${p}-host`} value=${st.host} required maxlength="253" spellcheck=${false}
              placeholder="127.0.0.1 or gpu-1.example.net" onInput=${(e) => this.setState({ host: e.currentTarget.value })} /></div>
          <div class="pe-field"><label for=${`${p}-port`}>Port</label>
            <input id=${`${p}-port`} value=${st.port} inputmode="numeric" maxlength="5"
              onInput=${(e) => this.setState({ port: e.currentTarget.value.replace(/[^0-9]/g, "") })} /></div>
          <div class="pe-field"><label for=${`${p}-scheme`}>Protocol</label>
            <select id=${`${p}-scheme`} value=${st.scheme} onChange=${(e) => this.setState({ scheme: e.currentTarget.value })}>
              <option value="HTTP">HTTP</option><option value="HTTPS">HTTPS</option></select></div>` : null}
        ${provider.key_required || st.proxy ? html`<${KeyField} p=${p} provider=${provider} st=${st} set=${set} />` : null}
      </div>
      ${provider.own_host ? html`<p class="hint ai-local-hint"><${Icon} name="info" /> On that machine:
        ${" "}<code>ollama serve</code>, then pull a model that can call tools, e.g.${" "}
        <code>ollama pull ${provider.example_model}</code>. To reach it from this appliance, start it with
        ${" "}<code>OLLAMA_HOST=0.0.0.0</code>.${st.proxy ? null : html`${" "}<button type="button" class="linkish"
          onClick=${() => set({ proxy: true })}>A proxy in front of it asks for a token?</button>`}</p>` : null}
      <label class="check-row"><input type="checkbox" checked=${st.agents}
        onChange=${(e) => this.setState({ agents: e.currentTarget.checked })} />
        <span>Let agents run on it — asked to confirm right after storing it</span></label>
      ${provider.key_required || st.proxy ? html`<p class="hint"><${Icon} name="vault" /> Sealed twice and bound to
        ${" "}${provider.own_host ? "where it runs" : `${provider.host}`}; agents never receive it.</p>` : null}
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy || (provider.key_required && !st.key)}>
          ${st.busy ? "Storing…" : `Connect ${provider.label}`}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

export class AiProviders extends Component {
  constructor(props) {
    super(props);
    this.state = { adding: null, note: null };
  }

  done(saved, agents, provider) {
    this.setState({ adding: null, note: agents
      ? `“${saved.name}” is connected and agents may run on it: hire one under Agents.`
      : `“${saved.name}” is stored. Agents stay off it until you let them.` });
    this.props.onChanged();
    // A local Ollama opens on what it has: its models, and which can work a case.
    if (provider.own_host) this.props.onOpen(saved.id);
  }

  card(provider, keys) {
    const writable = can("secrets.write");
    const live = keys.filter((k) => k.agent_access).length;
    return html`<li key=${provider.id} class=${cx("ai-card", keys.length && "ai-card-on")}>
      <div class="ai-card-head">
        <${BrandMark} brand=${provider.id} size=${44} />
        <div class="ai-card-text"><b>${provider.label}</b><span class="muted">${provider.product}${
          provider.own_host ? " · no key" : ""}</span></div>
      </div>
      <span class=${cx("ai-state", keys.length ? "ai-state-on" : "ai-state-off")}>
        ${keys.length ? `${keys.length} ${provider.own_host ? "server" : "key"}${keys.length === 1 ? "" : "s"} · ${live} for agents`
          : "Not connected"}</span>
      ${writable ? html`<button type="button" class=${keys.length ? "" : "primary"}
        aria-label=${`${keys.length ? (provider.own_host ? "Add a server for" : "Add a key for") : "Connect"} ${provider.label}`}
        onClick=${() => this.setState({ adding: provider.id, note: null })}>${keys.length
          ? (provider.own_host ? "Add a server" : "Add a key") : "Connect"}</button>` : null}
    </li>`;
  }

  render({ data, selected, onOpen }, { adding, note }) {
    const providers = data.catalogue.providers || [];
    const keys = data.secrets.filter((s) => providerOf(s.kind));
    const of = (id) => keys.filter((k) => providerOf(k.kind) === id);
    const open = providers.find((p) => p.id === adding);
    return html`<div class="ai-providers">
      <p class="hint ai-intro"><${Icon} name="sparkle" /> The models the NOC's agents think with. A cloud provider
        takes its key: sealed twice, bound to the provider's API, and no agent is ever shown it — the
        appliance calls the provider for it, and the audit log names which agent did. A local Ollama
        takes no key: say where it runs, and its models are listed.</p>
      <ul class="ai-grid">${providers.map((p) => this.card(p, of(p.id)))}</ul>
      ${note ? html`<p class="hint" role="status">${note}</p>` : null}
      ${open ? html`<${Connect} key=${open.id} provider=${open} secondFactor=${data.second_factor}
        onDone=${(saved, agents) => this.done(saved, agents, open)} onCancel=${() => this.setState({ adding: null })} />` : null}
      <h3 class="ai-keys-h">Connected</h3>
      ${keys.length ? html`<div class="table-scroll list-view"><table class="data secrets-table">
        <thead><tr><th scope="col">Status</th><th scope="col">Name</th><th scope="col">Endpoint</th>
          <th scope="col">Agents</th><th scope="col" class="wide-only">Stored</th></tr></thead>
        <tbody>${keys.map((s) => html`<tr key=${s.id} class=${cx(selected === s.id && "row-selected")}>
          <td><${StatusLight} status=${s.status} /></td>
          <td><span class="secret-name"><${BrandMark} brand=${providerOf(s.kind)} size=${28} />
            <span><button type="button" class="linkish secret-open" onClick=${() => onOpen(s.id)}
              aria-expanded=${selected === s.id ? "true" : "false"}>${s.name}</button>
            <span class="muted secret-kind">${s.kind_label}</span></span></span></td>
          <td class="mono">${s.host}:${s.port}</td>
          <td>${s.agent_access ? html`<span class="badge badge-warn">on</span>` : html`<span class="muted">off</span>`}</td>
          <td class="wide-only" title=${timeTitle(s.created_at)}>${relative(s.created_at)}</td>
        </tr>`)}</tbody></table></div>`
        : html`<p class="muted">No provider is connected yet: pick one above.</p>`}
    </div>`;
  }
}
