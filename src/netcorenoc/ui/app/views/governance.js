/* Governance: who may DO what (capabilities) and who may SEE which network elements (scope).
 *
 * Both are edited as JSON because a policy is a small reviewable document and a form would hide
 * its shape. Both show what the policy actually RESOLVES to, because "what did I just do?" is the
 * question an admin needs answered and a stored document does not answer it.
 *
 * The two warnings on this screen are controls, not decoration:
 *
 *   * a policy can only take capabilities away. The compiled ceiling is the maximum a role may
 *     ever hold, so an entry above it has no effect — and an admin always keeps the capabilities
 *     needed to repair this screen, which is why a well-formed policy cannot brick the appliance.
 *   * **visibility scoping is a presentation control and is NOT tenant isolation.** Correlation
 *     still learns across every network element and a situation may still form across a boundary
 *     a principal cannot see. Saying that here, every time, is the only thing standing between
 *     this feature and someone selling it as multi-tenancy.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loading, Failed, DataTable, SectionHeading, TimeCell, cell } from "../widgets.js";
import { can } from "../session.js";
import { plural } from "../format.js";

export class Governance extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", caps: null, scope: null, error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  async reload() {
    this.setState({ status: "loading" });
    try {
      const [caps, scope] = await Promise.all([get("/api/rbac"), get("/api/scope")]);
      this.setState({ status: "ready", caps, scope });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  render(_props, { status, caps, scope, error }) {
    if (status === "loading") return html`<${Loading} label="Reading the governance policies" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="the governance policies" />`;
    }
    return html`<div class="governanceview">
      <p class="hint">Restrict what each role or principal may <b>do</b> and <b>see</b>. With no
        policy stored, the appliance behaves exactly as it does today — most operators never need
        this screen.</p>

      <section class="panel-block">
        <${SectionHeading} title="Capabilities"
          hint=${"A policy can only take capabilities away, never add them. Each role's compiled " +
                 "ceiling is the maximum it may ever hold, so an entry above the ceiling has no " +
                 "effect, and an admin always keeps what is needed to repair this screen."} />
        ${caps.malformed ? html`<p class="err" role="alert">
          The stored capability policy could not be read (${caps.malformed_reason}).
          Authorization has fallen back to the built-in role permissions — <b>nobody has gained
          anything</b>. Fix or clear it below.</p>` : null}
        <${DataTable} columns=${[
          { key: "role", label: "role" },
          { key: "held", label: "capabilities held", numeric: true },
          { key: "removed", label: "removed by policy" },
        ]} rows=${["viewer", "editor", "admin"].map((role) => {
          const resolved = caps.resolved[role] || [];
          const ceiling = caps.ceiling[role] || [];
          const removed = ceiling.filter((c) => !resolved.includes(c));
          return {
            key: role,
            cells: {
              role: html`<b>${role}</b>`,
              held: `${resolved.length} / ${ceiling.length}`,
              removed: removed.length
                ? html`<span class="mono">${removed.join(", ")}</span>`
                : html`<span class="muted">(full ceiling)</span>`,
            },
          };
        })} />
        <${PolicyEditor} kind="rbac" policy=${caps} onChanged=${this.reload}
                         writable=${can("rbac.write")} />
        <${History} history=${caps.history} kind="rbac" onChanged=${this.reload}
                    writable=${can("rbac.write")} />
      </section>

      <section class="panel-block">
        <${SectionHeading} title="Visibility scope"
          hint=${"Which network elements a viewer or editor may see. Selectors: ne:<id>, an " +
                 "exact address, a CIDR, or an address glob (10.0.*). A selector never matches " +
                 "the operator label, which the scoped role can itself write. Admins are never " +
                 "scoped, so a mistake here is repairable."} />
        <p class="warnbox"><b>Visibility scoping is a presentation control and is NOT tenant
          isolation.</b> Correlation still learns across every network element, and a situation
          may still form across a boundary a principal cannot see — its members are then hidden
          from them and shown as a redacted count, not prevented from correlating.</p>
        ${scope.malformed ? html`<p class="err" role="alert">
          The stored scope could not be read (${scope.malformed_reason}). Viewers and editors are
          seeing nothing until it is fixed or cleared.</p>` : null}
        <${DataTable} columns=${[
          { key: "role", label: "role" },
          { key: "sees", label: "sees" },
        ]} rows=${["viewer", "editor"].map((role) => {
          const ids = (scope.resolved_ne_ids || {})[role] || [];
          return {
            key: role,
            cells: {
              role: html`<b>${role}</b>`,
              sees: scope.configured
                ? `${ids.length} of ${scope.ne_count} network elements`
                : `all ${plural(scope.ne_count, "network element")} (no policy)`,
            },
          };
        })} />
        <${PolicyEditor} kind="scope" policy=${scope} onChanged=${this.reload}
                         writable=${can("scope.write")} />
        <${History} history=${scope.history} kind="scope" onChanged=${this.reload}
                    writable=${can("scope.write")} />
      </section>
    </div>`;
  }
}

const BLANK = JSON.stringify({ version: 1, roles: {}, principals: {} }, null, 2);

/* The two policy routes, spelled out.
 *
 * These were composed — `` post(`/api/${kind}`) `` — which reads fine and is worse than it looks:
 * a route assembled from a variable is a route **no static reader can attribute**, so nothing
 * outside a running browser could say which capability this screen's writes require.
 * `tests/test_security_ui.py::test_mutating_controls_are_behind_capability_guards` resolves every
 * write to its `rbac.ROUTE_PERMISSIONS` entry and refuses one it cannot resolve, which is what
 * found this. Two literals cost nothing and make the screen auditable from the source.
 */
const POLICY_PATH = { rbac: "/api/rbac", scope: "/api/scope" };

class PolicyEditor extends Component {
  constructor(props) {
    super(props);
    this.state = {
      text: props.policy.active ? props.policy.active.document : BLANK,
      error: "", busy: false,
    };
  }

  async write(body) {
    this.setState({ busy: true, error: "" });
    try {
      await post(POLICY_PATH[this.props.kind], body);
      this.setState({ busy: false });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  apply() {
    let document_;
    try { document_ = JSON.parse(this.state.text); }
    catch (parseError) { this.setState({ error: `Not valid JSON: ${parseError.message}` }); return; }
    this.write({ document: document_, note: "" });
  }

  /* `writable` is a capability, not a styling choice.
   *
   * Reading a governance policy and rewriting one are separate capabilities on purpose, and a
   * stored policy can leave a principal holding `rbac.read` without `rbac.write` — narrowing is
   * the whole feature. Offering Apply to that principal would promise something the appliance
   * refuses, and would put a denied-and-audited row in the log for a control the console should
   * never have drawn. So the controls are ABSENT, and the screen says why rather than showing a
   * greyed button that reads as "you may not".
   */
  render({ kind, writable }, { text, error, busy }) {
    return html`<div class="policy-editor">
      <label for=${`pol-${kind}`}>Policy document (JSON)</label>
      <textarea id=${`pol-${kind}`} class="govjson" rows="8" value=${text} readonly=${!writable}
                onInput=${(e) => this.setState({ text: e.target.value })}></textarea>
      ${writable ? html`<div class="fb">
        <button type="button" disabled=${busy} onClick=${() => this.apply()}>Apply</button>
        <button type="button" disabled=${busy}
                onClick=${() => this.write({ clear: true })}>Clear policy</button>
      </div>` : html`<p class="hint">You can read this policy but not change it: your account
        holds <code class="mono">${kind}.read</code>${" and not "}
        <code class="mono">${kind}.write</code>.</p>`}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </div>`;
  }
}

function History({ history, kind, onChanged, writable }) {
  return html`<div class="govhistory">
    <p class="hint">History is append-only: rolling back moves a pointer and never edits or
      deletes a version.</p>
    <${DataTable} columns=${[
      { key: "id", label: "version", numeric: true },
      { key: "by", label: "by" },
      { key: "when", label: "when" },
      { key: "note", label: "note" },
      { key: "action", label: "" },
    ]} rows=${(history || []).map((row) => ({
      key: row.id,
      cells: {
        id: html`<span class="mono">${row.id}${row.active ? " (active)" : ""}</span>`,
        by: row.created_by || "—",
        when: cell(html`<${TimeCell} ts=${row.created_at} />`),
        note: row.note || "",
        action: row.active || !writable ? "" : html`<button type="button"
          onClick=${async () => { await post(POLICY_PATH[kind], { policy_id: row.id }); onChanged(); }}>
          Roll back</button>`,
      },
    }))} />
  </div>`;
}
