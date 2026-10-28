/* Hiring an agent, and changing one (ADRs #465, #468) — an admin's (`agents.manage`).
 *
 * Paperclip's agent configuration, in five parts: who it is (name, title, mark), its place in the
 * NOC (role, tier, manager, whether cases start with it), how it runs (the adapter and its
 * settings), what it is told (instructions — served to it as the MCP prompt `noc_agent`), and its
 * limits. The catalogue (roles, marks, adapters, models) is the server's, read with the list, and
 * the server checks every value again. A change sends only what changed, with the version read.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { AgentGlyph } from "./nocui.js";

const HEARTBEATS = [[0, "Off — woken by cases only"], [300, "Every 5 minutes"],
  [900, "Every 15 minutes"], [1800, "Every 30 minutes"], [3600, "Every hour"],
  [21600, "Every 6 hours"], [86400, "Once a day"]];
const ESCALATIONS = [[0, "The handbook's — critical 30 min, major 1 h, minor 2 h"],
  [900, "15 minutes"], [1800, "30 minutes"], [3600, "1 hour"], [7200, "2 hours"], [14400, "4 hours"]];
const TIMEOUTS = [[300, "5 minutes"], [900, "15 minutes"], [1800, "30 minutes"], [3600, "1 hour"],
  [7200, "2 hours"]];

function initial(agent) {
  const a = agent || {};
  const config = a.adapter_config || {};
  return {
    name: a.name || "", title: a.title || "", role: a.role || "engineer", icon: a.icon || "bot",
    tier: a.tier || 1, reports_to: a.reports_to || 0, entry: Boolean(a.entry),
    adapter: a.adapter || "claude", instructions: a.instructions || "",
    capabilities: a.capabilities || "", wake_on_assignment: a.wake_on_assignment !== false,
    heartbeat_s: a.heartbeat_s || 0, escalate_after_s: a.escalate_after_s || 0,
    run_timeout_s: a.run_timeout_s || 900, budget: a.budget_month_cents ? a.budget_month_cents / 100 : 0,
    model: config.model || "claude-opus-5-5", effort: config.effort || "high",
    secret_id: config.secret_id || "", max_turns: config.max_turns || 20,
    url: config.url || "", timeout_s: config.timeout_s || 10,
  };
}

function adapterConfig(st) {
  if (st.adapter === "claude") {
    return { model: st.model, effort: st.effort, secret_id: Number(st.secret_id) || null,
      max_turns: Number(st.max_turns) || 20 };
  }
  if (st.adapter === "webhook") return { url: st.url.trim(), timeout_s: Number(st.timeout_s) || 10 };
  return {};
}

/** The request body: everything for a hire; for a change, what differs from `agent`. */
function body(st, agent) {
  const all = {
    name: st.name.trim(), title: st.title.trim(), role: st.role, icon: st.icon,
    tier: Number(st.tier), reports_to: Number(st.reports_to) || (agent ? 0 : null),
    entry: st.entry, adapter: st.adapter, adapter_config: adapterConfig(st),
    instructions: st.instructions, capabilities: st.capabilities,
    wake_on_assignment: st.wake_on_assignment, heartbeat_s: Number(st.heartbeat_s),
    escalate_after_s: Number(st.escalate_after_s) || (agent ? 0 : null),
    run_timeout_s: Number(st.run_timeout_s), budget_month_cents: Math.round(Number(st.budget) * 100) || 0,
  };
  if (!agent) return all;
  const was = body(initial(agent), null);
  const changed = { version: agent.version };
  for (const [key, value] of Object.entries(all)) {
    const before = key === "reports_to" || key === "escalate_after_s" ? was[key] || 0 : was[key];
    if (JSON.stringify(value) !== JSON.stringify(before)) changed[key] = value;
  }
  return changed;
}

function choices(pairs, value) {
  const list = pairs.some(([v]) => v === Number(value)) ? pairs : [...pairs, [Number(value), `${value} s`]];
  return list.map(([v, label]) => html`<option key=${v} value=${v}>${label}</option>`);
}

export class AgentForm extends Component {
  constructor(props) {
    super(props);
    this.state = { ...initial(props.agent), busy: false, error: null, keys: null };
  }

  componentDidMount() { if (this.state.adapter === "claude") this.readKeys(); }

  /** The API tokens agents may use, for the `claude` adapter's key — a custodian's list. */
  async readKeys() {
    if (this.state.keys) return;
    try {
      const vault = await get("/api/noc/secrets");
      this.setState({ keys: vault.secrets.filter((s) => s.kind === "api_token" && s.agent_access) });
    } catch {
      this.setState({ keys: [] });
    }
  }

  set(key) {
    return (e) => {
      const target = e.currentTarget;
      this.setState({ [key]: target.type === "checkbox" ? target.checked : target.value });
      if (key === "adapter" && target.value === "claude") this.readKeys();
    };
  }

  async submit(event) {
    event.preventDefault();
    const { agent } = this.props;
    const payload = body(this.state, agent);
    if (agent && Object.keys(payload).length === 1) return this.props.onCancel();
    this.setState({ busy: true, error: null });
    try {
      const answer = await post(agent ? `/api/noc/agents/${agent.id}` : "/api/noc/agents", payload);
      this.setState({ busy: false });
      this.props.onSaved(answer);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
    return null;
  }

  field(id, label, control, hint, wide) {
    return html`<div class=${cx("pe-field", wide && "case-wide")}>
      <label for=${id}>${label}</label>${control}${hint ? html`<p class="hint">${hint}</p>` : null}
    </div>`;
  }

  adapterSettings(st, catalogue) {
    const p = "agent-form";
    if (st.adapter === "claude") {
      const keys = st.keys;
      return html`<div class="pe-fields">
        ${this.field(`${p}-model`, "Model", html`<select id=${`${p}-model`} value=${st.model}
          onChange=${this.set("model")}>${catalogue.models.map((m) => html`<option key=${m} value=${m}>${m}</option>`)}</select>`)}
        ${this.field(`${p}-effort`, "Effort", html`<select id=${`${p}-effort`} value=${st.effort}
          onChange=${this.set("effort")}>${catalogue.efforts.map((m) => html`<option key=${m} value=${m}>${m}</option>`)}</select>`,
          "How hard it thinks before acting: higher is slower and costs more.")}
        ${this.field(`${p}-key`, "Anthropic API key", keys === null
          ? html`<p class="muted" id=${`${p}-key`}>Reading the vault…</p>`
          : html`<select id=${`${p}-key`} required value=${st.secret_id} onChange=${this.set("secret_id")}>
              <option value="">Choose a credential…</option>
              ${keys.map((k) => html`<option key=${k.id} value=${k.id}>${k.name}</option>`)}
            </select>`,
          "An API token in Secrets with agent use on. The appliance unseals it for each run; the agent never sees it.")}
        ${this.field(`${p}-turns`, "Most steps per run", html`<input id=${`${p}-turns`} type="number"
          min="1" max="60" value=${st.max_turns} onInput=${this.set("max_turns")} />`)}
      </div>`;
    }
    if (st.adapter === "webhook") {
      return html`<div class="pe-fields">
        ${this.field(`${p}-url`, "Wake-up URL", html`<input id=${`${p}-url`} type="url" required
          placeholder="https://agents.example.net/netcorenoc" value=${st.url} onInput=${this.set("url")} />`,
          "A signed POST says why it is woken — the case, the run — and never carries a credential.", true)}
        ${this.field(`${p}-timeout`, "Wait for an answer (s)", html`<input id=${`${p}-timeout`}
          type="number" min="1" max="30" value=${st.timeout_s} onInput=${this.set("timeout_s")} />`)}
      </div>`;
    }
    return html`<p class="hint">It connects over MCP with its key when it decides to; the appliance
      never calls it. Each visit is recorded as a run.</p>`;
  }

  render({ agent, catalogue, agents, onCancel }, st) {
    const p = "agent-form";
    const managers = (agents || []).filter((a) => a.status !== "terminated" && (!agent || a.id !== agent.id));
    return html`<form class="agent-form" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <header class="agent-form-head">
        <${AgentGlyph} agent=${{ role: st.role, icon: st.icon }} size="lg" />
        <div><h2>${agent ? `Configure ${agent.name}` : "Hire an agent"}</h2>
          <p class="muted">${agent ? "Only what you change is sent." : "It joins the NOC with a key of its own: a token, never a password."}</p></div>
      </header>

      <fieldset class="form-section"><legend>Who it is</legend>
        <div class="pe-fields">
          ${this.field(`${p}-name`, "Name", html`<input id=${`${p}-name`} required maxlength="60"
            value=${st.name} onInput=${this.set("name")} />`, "Its key and every act are recorded under it.")}
          ${this.field(`${p}-title`, "Title", html`<input id=${`${p}-title`} maxlength="80"
            placeholder="Optical transport engineer" value=${st.title} onInput=${this.set("title")} />`)}
        </div>
        <div class="icon-pick" role="radiogroup" aria-label="Its mark">
          ${catalogue.icons.map((icon) => html`<label key=${icon} class=${cx("icon-opt", st.icon === icon && "on")}>
            <input type="radio" name=${`${p}-icon`} value=${icon} checked=${st.icon === icon}
              onChange=${this.set("icon")} class="visually-hidden" />
            <${AgentGlyph} agent=${{ role: st.role, icon }} size="sm" /><span class="visually-hidden">${icon}</span>
          </label>`)}
        </div>
      </fieldset>

      <fieldset class="form-section"><legend>Its place in the NOC</legend>
        <div class="pe-fields">
          ${this.field(`${p}-role`, "Role", html`<select id=${`${p}-role`} value=${st.role} onChange=${this.set("role")}>
            ${Object.entries(catalogue.roles).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}</select>`)}
          ${this.field(`${p}-tier`, "Tier", html`<select id=${`${p}-tier`} value=${st.tier} onChange=${this.set("tier")}>
            ${[1, 2, 3, 4].map((t) => html`<option key=${t} value=${t}>Tier ${t}</option>`)}</select>`,
            "A case climbs a tier each time it is escalated.")}
          ${this.field(`${p}-boss`, "Reports to", html`<select id=${`${p}-boss`} value=${st.reports_to}
            onChange=${this.set("reports_to")}>
            <option value="0">The NOC team (people)</option>
            ${managers.map((a) => html`<option key=${a.id} value=${a.id}>${a.name} — ${a.role_label}, Tier ${a.tier}</option>`)}
          </select>`, "Its cases climb to this manager; past the top, to the NOC team.")}
        </div>
        <label class="check-row"><input type="checkbox" checked=${st.entry} onChange=${this.set("entry")} />
          Entry point: cases from the queue start with it, at Tier 1</label>
      </fieldset>

      <fieldset class="form-section"><legend>How it runs</legend>
        <div class="adapter-pick" role="radiogroup" aria-label="How it runs">
          ${Object.entries(catalogue.adapters).map(([k, v]) => html`<label key=${k}
            class=${cx("adapter-opt", st.adapter === k && "on")}>
            <input type="radio" name=${`${p}-adapter`} value=${k} checked=${st.adapter === k}
              onChange=${this.set("adapter")} />
            <span><b>${v.split(" — ")[0]}</b><span class="muted">${v.split(" — ")[1] || ""}</span></span>
          </label>`)}
        </div>
        ${agent && (st.adapter === "claude") !== (agent.adapter === "claude") ? html`<p class="warnbox">
          Moving between the appliance and elsewhere gives ${agent.name} a new key; the old one stops working.</p>` : null}
        ${this.adapterSettings(st, catalogue)}
      </fieldset>

      <fieldset class="form-section"><legend>What it is told</legend>
        ${this.field(`${p}-instructions`, "Instructions", html`<textarea id=${`${p}-instructions`} rows="10"
          maxlength="65536" value=${st.instructions} onInput=${this.set("instructions")}
          placeholder="Which cases are yours, how you work one, what you check first, when you escalate."></textarea>`,
          "Served to it as the MCP prompt noc_agent, before how the NOC works here.", true)}
        ${this.field(`${p}-caps`, "What it can do", html`<textarea id=${`${p}-caps`} rows="3" maxlength="2000"
          value=${st.capabilities} onInput=${this.set("capabilities")}
          placeholder="Ciena SAOS and MikroTik RouterOS; optical power levels; LOS and link flaps."></textarea>`,
          "Read by its managers when they route a case to it.", true)}
      </fieldset>

      <fieldset class="form-section"><legend>When it wakes, and its limits</legend>
        <label class="check-row"><input type="checkbox" checked=${st.wake_on_assignment}
          onChange=${this.set("wake_on_assignment")} /> Wake it when a case is routed to it</label>
        <div class="pe-fields">
          ${this.field(`${p}-beat`, "Heartbeat", html`<select id=${`${p}-beat`} value=${st.heartbeat_s}
            onChange=${this.set("heartbeat_s")}>${choices(HEARTBEATS, st.heartbeat_s)}</select>`)}
          ${this.field(`${p}-esc`, "Escalate when not concluded within", html`<select id=${`${p}-esc`}
            value=${st.escalate_after_s} onChange=${this.set("escalate_after_s")}>${choices(ESCALATIONS, st.escalate_after_s)}</select>`)}
          ${this.field(`${p}-limit`, "A run may last", html`<select id=${`${p}-limit`} value=${st.run_timeout_s}
            onChange=${this.set("run_timeout_s")}>${choices(TIMEOUTS, st.run_timeout_s)}</select>`)}
          ${this.field(`${p}-budget`, "Monthly budget (US$)", html`<input id=${`${p}-budget`} type="number"
            min="0" step="0.01" value=${st.budget} onInput=${this.set("budget")} />`,
            "0 is no limit. Reached, the agent is paused until an editor resumes it.")}
        </div>
      </fieldset>

      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy || !st.name.trim()}>
          ${st.busy ? "Saving…" : agent ? "Save the changes" : "Hire the agent"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}
