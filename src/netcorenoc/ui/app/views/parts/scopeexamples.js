/* People & access → Visibility: what a policy is for, shown as policies (v0.30.0).
 *
 * The editor takes a small JSON document, and a blank one teaches nothing. These are the common
 * shapes, each a complete document that "Use this" copies into the editor — never applies: the
 * admin still reads it, replaces the placeholder ids and presses Apply. Beside them, the four selector forms and
 * the ids of the people and tokens a principal entry names, so nobody has to guess `user:7`.
 */

import { html } from "../../dom.js";

const doc = (body) => JSON.stringify({ version: 1, roles: {}, principals: {}, ...body }, null, 2);

/**
 * [title, when to use it, the document]. A role's entry narrows everyone with that role. The ids are
 * placeholders on purpose — the list below the examples gives the real ones — so "Use this" never
 * produces a policy that silently names somebody the admin did not pick.
 */
export function examples() {
  const user = "user:7";
  const token = "token:3";
  return [
    ["Viewers see the core only",
      "Every viewer sees the core network's management range; editors and admins see everything.",
      doc({ roles: { viewer: ["10.0.0.0/16"] } })],
    ["A regional team sees its region",
      "One person — a regional engineer — sees one region's addresses, whatever their role.",
      doc({ principals: { [user]: ["10.20.0.0/16", "10.21.*"] } })],
    ["A contractor sees two devices",
      "Exactly two elements: one by its id on the Entities screen, one by address.",
      doc({ principals: { [user]: ["ne:42", "192.0.2.10"] } })],
    ["A wall display sees one site",
      "A read-only service token for a NOC wall, limited to one site's devices.",
      doc({ principals: { [token]: ["172.16.8.0/22"] } })],
  ];
}

const SELECTORS = [
  ["ne:42", "one element, by the id the Entities screen shows"],
  ["192.0.2.10", "one element, by its management address"],
  ["10.20.0.0/16", "every element in an address range (CIDR)"],
  ["10.20.*", "a wildcard over addresses — names and labels never match"],
];

export function ScopeExamples({ users, tokens, onUse, writable }) {
  const people = users || [];
  return html`<section class="scope-help">
    <h4>Examples</h4>
    <div class="scope-examples">
      ${examples().map(([title, why, text]) => html`<div key=${title} class="scope-example">
        <b>${title}</b><p class="muted">${why}</p>
        <pre class="token-example">${text}</pre>
        ${writable ? html`<button type="button" class="linkish" onClick=${() => onUse(text)}>Use this</button>` : null}
      </div>`)}
    </div>
    <h4>Selectors</h4>
    <table class="data scope-selectors"><tbody>${SELECTORS.map(([form, means]) => html`<tr key=${form}>
      <td class="mono">${form}</td><td>${means}</td></tr>`)}</tbody></table>
    ${people.length ? html`<details class="scope-ids"><summary>Ids for principal entries</summary>
      <ul class="mini-list">${people.map((u) => html`<li key=${u.id}><code class="mono">user:${u.id}</code>
        <span>${u.display_name || u.username}</span><span class="muted">${u.role}</span></li>`)}
      ${(tokens || []).filter((t) => !t.revoked).map((t) => html`<li key=${`t${t.id}`}><code class="mono">token:${t.id}</code>
        <span>${t.name}</span><span class="muted">${t.role} token</span></li>`)}</ul>
      <p class="hint">Admins are never narrowed by visibility; an entry naming one has no effect.</p>
    </details>` : null}
  </section>`;
}
