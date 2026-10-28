/* An agent's key, shown once — and what an MCP client needs to reach this appliance (ADR #466).
 *
 * The key is an agent's identity, a service token named as the agent: this appliance keeps a hash
 * of it and nothing else, so the answer to a hire (or a new key) is the only time it exists in
 * clear. An agent the appliance runs itself (`claude`) is never shown one: its key stays sealed.
 *
 * The client configuration reads the key from the environment rather than holding it, so the file
 * that names this endpoint can be shared, and the secret lives only where the agent runs.
 */

import { html, Component } from "../../dom.js";

const ENV = "NETCORENOC_AGENT_KEY";

function endpoint(path) {
  const origin = typeof location !== "undefined" && location.origin ? location.origin : "";
  return `${origin}${path || "/api/mcp"}`;
}

/** A Claude Code `.mcp.json` entry for this appliance. */
export function clientConfig(path) {
  return JSON.stringify({
    mcpServers: {
      netcorenoc: {
        type: "http",
        url: endpoint(path),
        headers: { Authorization: `Bearer \${${ENV}}` },
      },
    },
  }, null, 2);
}

class Copy extends Component {
  async copy() {
    try {
      await navigator.clipboard.writeText(this.props.text);
      this.setState({ said: "Copied." });
    } catch {
      this.setState({ said: "Select it and copy it by hand." });
    }
  }

  render({ label }, { said }) {
    return html`<span class="copy-row">
      <button type="button" onClick=${() => this.copy()}>${label || "Copy"}</button>
      ${said ? html`<span class="muted" role="status">${said}</span>` : null}
    </span>`;
  }
}

/** The once-only panel after a hire, or after a new key. */
export function KeyOnce({ hired, mcpPath, onDone, title }) {
  const { agent, token, webhook_key: webhookKey } = hired;
  return html`<section class="panel-block key-once" aria-labelledby="key-once-heading">
    <h3 id="key-once-heading">${title || `${agent.name} is hired`}</h3>
    ${token ? html`
      <p><b>Its key, shown once.</b> Store it where ${agent.name} runs — as <code>${ENV}</code> in
        its environment. This appliance keeps only a hash: lost, it is replaced, never shown again.</p>
      <pre class="key-value mono">${token}</pre>
      <${Copy} text=${token} label="Copy the key" />` : html`
      <p>The appliance runs ${agent.name} itself. Its key stays sealed in the vault, and nobody —
        no person, no agent — is shown it.</p>`}
    ${webhookKey ? html`
      <p><b>Its webhook signing key, shown once.</b> Each wake-up is a POST signed with it in
        the${" "}<code>X-NetCoreNOC-Signature</code> header; ${agent.name} verifies it before acting.</p>
      <pre class="key-value mono">${webhookKey}</pre>
      <${Copy} text=${webhookKey} label="Copy the signing key" />` : null}
    ${token ? html`<h4>Connect its MCP client</h4>
      <p class="hint">For Claude Code, in <code>.mcp.json</code>; any MCP client over Streamable HTTP
        takes the same URL and header.</p>
      <pre class="key-config mono">${clientConfig(mcpPath)}</pre>` : null}
    <div class="stepup-acts">
      <button type="button" class="primary" onClick=${onDone}>${token || webhookKey
        ? "I have stored it" : "Open its page"}</button>
    </div>
  </section>`;
}

/** How an agent reaches the NOC: the endpoint, its transport and its credential. */
export function McpConnect({ mcpPath }) {
  return html`<details class="panel-block mcp-connect">
    <summary><b>Connect an agent over MCP</b> <span class="muted">— ${endpoint(mcpPath)}</span></summary>
    <ul class="mcp-facts">
      <li><b>Transport</b> Streamable HTTP: one JSON-RPC message per POST, answered in JSON.</li>
      <li><b>Credential</b> the agent's own key as <code>Authorization: Bearer …</code> — never a
        person's session, never a device password.</li>
      <li><b>Tools</b> each a route of this API, called as the agent: authorized, scoped and audited
        as the route is, and a step of the agent's run.</li>
      <li><b>Instructions</b> the prompt <code>noc_agent</code>: what its manager wrote, then how the
        NOC works here.</li>
    </ul>
    <pre class="key-config mono">${clientConfig(mcpPath)}</pre>
    <${Copy} text=${clientConfig(mcpPath)} label="Copy the configuration" />
  </details>`;
}
