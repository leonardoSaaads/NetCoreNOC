/* How an agent runs, in the hiring form (ADRs #465, #471): where it thinks, and with whose key.
 *
 * Two questions, asked as a person asks them. **Here or elsewhere?** Here, the appliance runs it on
 * an AI provider — a card per provider, with its logo: Anthropic (the `claude` adapter), OpenAI,
 * Google AI Studio, xAI, Meta or a local Ollama (the `openai` adapter). Elsewhere, it is an agent
 * running on its own machine, woken by a signed webhook or coming over MCP when it decides to.
 * **With which key, and which model?** The key is one a custodian stored under Secrets → AI
 * providers and let agents use — offered by name, never shown; with none yet, the form says where
 * to add one. The model is the provider's own list, asked of it with that key, or typed.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { BrandMark } from "../../brands.js";
import { Icon } from "../../icons.js";

/** The adapter and provider a provider card stands for. */
export function runOf(provider) {
  return provider.adapter === "claude" ? { adapter: "claude", provider: "anthropic" }
    : { adapter: "openai", provider: provider.id };
}

/** The provider an agent's state names: Anthropic for `claude`, its own for `openai`. */
export function providerFor(st) {
  if (st.adapter === "claude") return "anthropic";
  return st.adapter === "openai" ? st.provider : null;
}

export class RunSettings extends Component {
  constructor(props) {
    super(props);
    this.state = { keys: null, models: null, asking: false, error: null };
  }

  componentDidMount() { if (providerFor(this.props.st)) this.readKeys(); }

  componentDidUpdate(previous) {
    if (providerFor(previous.st) !== providerFor(this.props.st)) this.setState({ models: null, error: null });
    if (providerFor(this.props.st) && this.state.keys === null) this.readKeys();
  }

  /** The provider keys agents may use — read once; filtered per provider when drawn. */
  async readKeys() {
    this.setState({ keys: [] });
    try {
      const vault = await get("/api/noc/secrets");
      this.setState({ keys: vault.secrets.filter((s) => s.agent_access
        && (s.kind.startsWith("ai_") || s.kind === "api_token")) });
    } catch {
      this.setState({ keys: [] });
    }
  }

  async ask() {
    const { st } = this.props;
    this.setState({ asking: true, error: null });
    try {
      const answer = await post(`/api/noc/secrets/${Number(st.secret_id)}/models`);
      this.setState({ asking: false, models: answer.models });
    } catch (error) {
      this.setState({ asking: false, error: error.message });
    }
  }

  cards(st, providers) {
    const here = providerFor(st);
    return html`<div class="run-pick" role="radiogroup" aria-label="Where it thinks">
      ${providers.map((pv) => html`<button type="button" key=${pv.id} role="radio" aria-checked=${here === pv.id}
          class=${cx("run-card", here === pv.id && "on")}
          onClick=${() => this.props.onChange({ ...runOf(pv), secret_id: "",
            model: pv.adapter === "claude" ? pv.models[0] : "" })}>
        <${BrandMark} brand=${pv.id} size=${36} />
        <span><b>${pv.label}</b><span class="muted">${pv.product}</span></span>
      </button>`)}
      ${[["webhook", "Webhook", "Runs elsewhere; woken by a signed POST"],
        ["external", "External", "Comes over MCP on its own"]].map(([k, label, what]) => html`<button type="button"
          key=${k} role="radio" aria-checked=${st.adapter === k} class=${cx("run-card", "run-elsewhere", st.adapter === k && "on")}
          onClick=${() => this.props.onChange({ adapter: k })}>
        <span class="run-elsewhere-mark" aria-hidden="true"><${Icon} name=${k === "webhook" ? "send" : "link"} /></span>
        <span><b>${label}</b><span class="muted">${what}</span></span>
      </button>`)}
    </div>`;
  }

  render({ st, catalogue, field, set }, { keys, models, asking, error }) {
    const p = "agent-form";
    const providers = catalogue.providers || [];
    const pid = providerFor(st);
    const provider = providers.find((x) => x.id === pid);
    const mine = (keys || []).filter((k) => k.kind === `ai_${pid}` || (pid === "anthropic" && k.kind === "api_token"));
    return html`<div class="run-settings">
      ${this.cards(st, providers)}
      ${provider ? html`<div class="pe-fields">
        ${field(`${p}-key`, `${provider.label} key`, keys === null
          ? html`<p class="muted" id=${`${p}-key`}>Reading the vault…</p>`
          : mine.length ? html`<select id=${`${p}-key`} required value=${st.secret_id} onChange=${set("secret_id")}>
              <option value="">Choose a key…</option>
              ${mine.map((k) => html`<option key=${k.id} value=${k.id}>${k.name}</option>`)}
            </select>`
          : html`<p class="warnbox" id=${`${p}-key`}>No ${provider.label} key agents may use yet.${" "}
              <a href="#/secrets?tab=ai">Connect ${provider.label} under Secrets → AI providers</a>.</p>`,
          "The appliance unseals it for each run and calls the provider; the agent never sees it.")}
        ${field(`${p}-model`, "Model", provider.adapter === "claude"
          ? html`<select id=${`${p}-model`} value=${st.model} onChange=${set("model")}>
              ${catalogue.models.map((m) => html`<option key=${m} value=${m}>${m}</option>`)}</select>`
          : html`<div class="model-row">
              <input id=${`${p}-model`} list=${`${p}-models`} required maxlength="200" spellcheck=${false}
                placeholder=${`e.g. ${provider.example_model}`} value=${st.model} onInput=${set("model")} />
              <datalist id=${`${p}-models`}>${(models || []).map((m) => html`<option key=${m} value=${m} />`)}</datalist>
              <button type="button" disabled=${!st.secret_id || asking} onClick=${() => this.ask()}>
                ${asking ? "Asking…" : models ? `${models.length} models` : `Ask ${provider.label}`}</button>
            </div>`,
          error || (provider.adapter === "claude" ? null : "As the provider names it; ask it for its list once a key is chosen."))}
        ${provider.adapter === "claude" ? field(`${p}-effort`, "Effort", html`<select id=${`${p}-effort`} value=${st.effort}
          onChange=${set("effort")}>${catalogue.efforts.map((m) => html`<option key=${m} value=${m}>${m}</option>`)}</select>`,
          "How hard it thinks before acting: higher is slower and costs more.") : null}
        ${field(`${p}-turns`, "Most steps per run", html`<input id=${`${p}-turns`} type="number"
          min="1" max="60" value=${st.max_turns} onInput=${set("max_turns")} />`)}
      </div>
      ${provider.adapter === "claude" ? null : html`<p class="hint">Its tokens are counted per run;
        spend is priced for Claude models only, so a budget does not stop it.</p>`}` : null}
      ${st.adapter === "webhook" ? html`<div class="pe-fields">
        ${field(`${p}-url`, "Wake-up URL", html`<input id=${`${p}-url`} type="url" required
          placeholder="https://agents.example.net/netcorenoc" value=${st.url} onInput=${set("url")} />`,
          "A signed POST says why it is woken — the case, the run — and never carries a credential.", true)}
        ${field(`${p}-timeout`, "Wait for an answer (s)", html`<input id=${`${p}-timeout`}
          type="number" min="1" max="30" value=${st.timeout_s} onInput=${set("timeout_s")} />`)}
      </div>` : null}
      ${st.adapter === "external" ? html`<p class="hint">It connects over MCP with its key when it decides
        to; the appliance never calls it. Each visit is recorded as a run.</p>` : null}
    </div>`;
  }
}
