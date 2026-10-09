/* People & access → Service tokens: how to use a token, and every call it can make (v0.30.0).
 *
 * The reference is read from the appliance's own OpenAPI schema (`GET /api/reference`, the document
 * `/openapi.json` serves), where every operation carries a
 * one-line summary, a group, and — from the authorization tables, never restated — the capability
 * it needs (`x-capability`) and whether its answer depends on visibility (`x-scope`). So it cannot
 * describe a route this appliance does not serve, and "can this token call it?" is answered from
 * the same set the server enforces: pick a role or a token and the list narrows to what it holds.
 *
 * Every example uses `$NETCORENOC_TOKEN`, never a real value: the value is shown once, at creation.
 */

import { html, Component, cx } from "../../dom.js";
import { get } from "../../api.js";
import { Loading, Failed, SectionHeading } from "../../widgets.js";
import { accessOf } from "./people.js";

const ORIGIN = () => (globalThis.location && globalThis.location.origin) || "https://<appliance>";
/** The reference reads in the order an integrator looks for things: the work first. */
const GROUP_ORDER = ["Situations", "Alarms", "Monitoring", "Network elements", "Trap catalogue",
  "Maintenance", "Models", "Account", "People", "Access policy", "Settings", "Audit",
  "Sign-in and recovery"];
const SCOPE_WORD = {
  scoped: "answer narrowed to the elements the caller may see",
  unscoped: "same answer for every caller of the role",
  admin_only: "admin capability; never narrowed",
  public: "no token needed",
};

/** The operations in a schema, flattened, in the schema's order. */
export function operations(schema) {
  const out = [];
  for (const [path, verbs] of Object.entries(schema.paths || {})) {
    if (!path.startsWith("/api")) continue;
    for (const [method, op] of Object.entries(verbs)) {
      out.push({ key: `${method} ${path}`, method: method.toUpperCase(), path, op,
        group: (op.tags || ["Other"])[0], capability: op["x-capability"] || null,
        scope: op["x-scope"] || "public" });
    }
  }
  return out;
}

function resolve(schema, node) {
  if (node && node.$ref) return schema.components.schemas[node.$ref.split("/").pop()];
  return node;
}

/** A small example value for a JSON schema node: defaults first, then the type's placeholder. */
export function example(schema, node, depth = 0) {
  const s = resolve(schema, node) || {};
  if (s.default !== undefined) return s.default;
  if (s.enum) return s.enum[0];
  if (s.anyOf) return example(schema, s.anyOf.find((x) => x.type !== "null") || {}, depth);
  if (s.type === "object" || s.properties) {
    if (depth > 2) return {};
    const required = new Set(s.required || []);
    return Object.fromEntries(Object.entries(s.properties || {})
      .filter(([name]) => required.has(name) || depth === 0)
      .map(([name, prop]) => [name, example(schema, prop, depth + 1)]));
  }
  if (s.type === "array") return [];
  if (s.type === "integer" || s.type === "number") return s.minimum ?? 0;
  if (s.type === "boolean") return false;
  return "…";
}

function curl(schema, item) {
  const lines = [`curl${item.method === "GET" ? "" : ` -X ${item.method}`}`];
  if (item.scope !== "public") lines.push(`-H "Authorization: Bearer $NETCORENOC_TOKEN"`);
  const body = item.op.requestBody && item.op.requestBody.content["application/json"];
  if (body) {
    lines.push(`-H "Content-Type: application/json"`);
    lines.push(`-d '${JSON.stringify(example(schema, body.schema))}'`);
  }
  lines.push(`"${ORIGIN()}${item.path}"`);
  return lines.join(" \\\n  ");
}

function Detail({ schema, item, minimum }) {
  const params = (item.op.parameters || []).map((p) => ({ ...p, schema: resolve(schema, p.schema) || {} }));
  return html`<div class="apiref-detail">
    <p><b>Needs</b>${" "}${item.capability ? html`<code class="mono">${item.capability}</code>
      ${" "}(${minimum[item.capability] || "?"} or above)` : "nothing — it is public"} ·
      ${" "}${SCOPE_WORD[item.scope] || item.scope}</p>
    ${params.length ? html`<table class="data apiref-params"><thead><tr><th>parameter</th><th>in</th>
      <th>type</th><th>required</th></tr></thead><tbody>${params.map((p) => html`<tr key=${p.name}>
      <td class="mono">${p.name}</td><td>${p.in}</td><td>${p.schema.type || (p.schema.anyOf ? "optional" : "")}</td>
      <td>${p.required ? "yes" : "no"}</td></tr>`)}</tbody></table>` : null}
    <pre class="token-example">${curl(schema, item)}</pre>
  </div>`;
}

export class ApiReference extends Component {
  constructor(props) {
    super(props);
    this.state = { schema: null, error: null, q: "", who: "", open: null };
  }

  async componentDidMount() {
    try { this.setState({ schema: await get("/api/reference") }); }
    catch (error) { this.setState({ error }); }
  }

  /** The capability set of the chosen role or token, or null for "every route". */
  held() {
    const { rbac, tokens } = this.props;
    const { who } = this.state;
    if (!who || !rbac) return null;
    if (who.startsWith("role:")) return new Set(rbac.resolved[who.slice(5)] || []);
    const token = (tokens || []).find((t) => `token:${t.id}` === who);
    return token ? accessOf(rbac, token.role, who).value : null;
  }

  render({ rbac, tokens }, { schema, error, q, who, open }) {
    if (error) return html`<${Failed} error=${error} what="the API schema" />`;
    if (!schema) return html`<${Loading} label="Reading the API schema" />`;
    const held = this.held();
    const needle = q.trim().toLowerCase();
    const items = operations(schema).filter((i) => (!held || i.scope === "public" || held.has(i.capability))
      && (!needle || `${i.method} ${i.path} ${i.op.summary || ""}`.toLowerCase().includes(needle)));
    const rank = (g) => (GROUP_ORDER.includes(g) ? GROUP_ORDER.indexOf(g) : GROUP_ORDER.length);
    const groups = [...new Set(items.map((i) => i.group))].sort((a, b) => rank(a) - rank(b));
    const minimum = (rbac && rbac.minimum_role) || {};
    return html`<section class="panel-block apiref">
      <${SectionHeading} title="API reference"
        hint=${`${items.length} calls. Read from this appliance's own OpenAPI schema, so it is always current.`} />
      <div class="apiref-bar">
        <input type="search" placeholder="Search calls" aria-label="Search calls" value=${q}
          onInput=${(e) => this.setState({ q: e.currentTarget.value })} />
        <label class="apiref-who"><span>Calls available to</span>
          <select value=${who} onChange=${(e) => this.setState({ who: e.currentTarget.value })}>
            <option value="">every route</option>
            ${rbac ? ["viewer", "editor", "admin"].map((r) => html`<option key=${r} value=${`role:${r}`}>a ${r} token</option>`) : null}
            ${(tokens || []).filter((t) => !t.revoked).map((t) => html`<option key=${t.id} value=${`token:${t.id}`}>token “${t.name}”</option>`)}
          </select></label>
        <a href="/openapi.json" target="_blank" rel="noopener">OpenAPI schema (JSON)</a>
      </div>
      ${groups.map((group) => html`<div key=${group} class="apiref-group">
        <h4>${group}</h4>
        <ul class="apiref-list">${items.filter((i) => i.group === group).map((i) => html`<li key=${i.key}>
          <button type="button" class=${cx("apiref-row", open === i.key && "on")} aria-expanded=${open === i.key}
            onClick=${() => this.setState({ open: open === i.key ? null : i.key })}>
            <span class=${`verb verb-${i.method.toLowerCase()}`}>${i.method}</span>
            <code class="mono apiref-path">${i.path}</code>
            <span class="apiref-sum">${i.op.summary}</span>
          </button>
          ${open === i.key ? html`<${Detail} schema=${schema} item=${i} minimum=${minimum} />` : null}
        </li>`)}</ul>
      </div>`)}
      ${items.length ? null : html`<p class="muted">No call matches.</p>`}
    </section>`;
  }
}

/** The three facts a first-time integrator needs, with a call that works for any token. */
export function QuickStart() {
  return html`<section class="panel-block apiref-start">
    <${SectionHeading} title="Use the API" />
    <ol class="apiref-steps">
      <li><b>Create a token</b> below and copy it — it is shown once. Its role is the most it can
        do; narrow it with <i>Access</i>.</li>
      <li><b>Send it as a bearer header.</b> Keep it in an environment variable, never in a script:
        <pre class="token-example">export NETCORENOC_TOKEN='…'
curl -H "Authorization: Bearer $NETCORENOC_TOKEN" ${ORIGIN()}/api/me</pre></li>
      <li><b>Find the call</b> in the reference below; each one shows what it needs and a ready${" "}
        <code class="mono">curl</code>${" "}command. Requests are limited to bursts of 30, then 10
        a second.</li>
    </ol>
  </section>`;
}
