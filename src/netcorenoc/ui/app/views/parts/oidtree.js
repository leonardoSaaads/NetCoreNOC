/* The branch browser: name a whole vendor branch in a few clicks (v0.22.0, item 18, #384).
 *
 * Pick a vendor, click down its arcs, and at any node give a name and a severity: every trap
 * beneath inherits them — **by arc, never by string prefix** (`…2011.1.2` covers `…2011.1.2.1` and
 * never `…2011.1.12`) — unless something more specific says otherwise. The class table beside it
 * shows, for every class, which rule is winning.
 *
 * The children come from the classes the appliance has seen and the rules anyone wrote; at the
 * enterprise root, from every vendor it can name, so a branch can be named before its first trap.
 */

import { html, Component } from "../../dom.js";
import { get, post, del } from "../../api.js";
import { Badge, SeverityChip } from "../../widgets.js";
import { band, count } from "../../format.js";

export const ROOT = "1.3.6.1.4.1";
const GRADES = ["critical", "major", "minor", "warning", "indeterminate"];
const RANK = { critical: 0, major: 1, minor: 2, warning: 3, indeterminate: 4 };

export class BranchBrowser extends Component {
  constructor(props) {
    super(props);
    this.state = { tree: null, error: null, name: "", severity: "", exact: false, busy: false,
                   jump: "" };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.node !== this.props.node) this.read();
  }

  async read() {
    try {
      const tree = await get(`/api/catalogue/tree?node=${encodeURIComponent(this.props.node)}`);
      this.setState({ tree, error: null, name: "", severity: "" });
    } catch (error) {
      this.setState({ error });
    }
  }

  async save(event) {
    event.preventDefault();
    const { name, severity, exact } = this.state;
    if (!name.trim() && !severity) return;
    this.setState({ busy: true, error: null });
    try {
      await post("/api/catalogue/rules", {
        oid: this.props.node, subtree: !exact, name: name.trim() || null, severity: severity || null,
      });
      this.setState({ busy: false });
      await this.read();
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  async remove(id) {
    try {
      await del(`/api/catalogue/rules/${id}`);
      await this.read();
      this.props.onChanged();
    } catch (error) {
      this.setState({ error });
    }
  }

  render({ node, onNode, editable }, { tree, error, name, severity, exact, busy, jump }) {
    const arcs = node.split(".");
    const rootDepth = node.startsWith(`${ROOT}.`) || node === ROOT ? ROOT.split(".").length : 1;
    const crumbs = arcs.slice(rootDepth - 1).map((arc, i) => ({
      arc, oid: arcs.slice(0, rootDepth + i).join("."),
    }));
    return html`<section class="panel-block oidtree" aria-label="Branches">
      <nav class="oid-crumbs" aria-label="Where you are in the OID tree">
        ${crumbs.map((c, i) => html`${i ? html`<span aria-hidden="true">›</span>` : null}
          <button type="button" key=${c.oid} class="tap" aria-current=${c.oid === node ? "location" : null}
            onClick=${() => onNode(c.oid)}>${i === 0 && c.oid === ROOT ? "enterprises" : c.arc}</button>`)}
        ${tree && tree.vendor ? html`<${Badge}>${tree.vendor}<//>` : null}
        <form class="oid-jump" onSubmit=${(e) => {
          e.preventDefault();
          const to = jump.trim().replace(/^\./, "");
          if (/^[0-9]+(\.[0-9]+)+$/.test(to)) onNode(to);
        }}>
          <label class="visually-hidden" for="oidJump">Go to OID</label>
          <input id="oidJump" placeholder="go to OID" value=${jump} inputmode="decimal"
                 onInput=${(e) => this.setState({ jump: e.target.value })} />
        </form>
      </nav>
      ${error ? html`<p class="err" role="alert">${error.message}</p>` : null}
      ${tree ? html`
        <p class="oid-here"><code class="mono">${node}</code>
          ${tree.name ? html`${" "}<b>${tree.name}</b>` : null}
          ${tree.severity ? html`${" "}<${SeverityChip} band=${band(RANK[tree.severity])} />` : null}
          <span class="muted">${" · "}${count(tree.classes_beneath)} classes beneath</span></p>
        ${tree.rules.map((rule) => html`<p class="oid-rule" key=${rule.id}>
          <${Badge} tone=${rule.source === "imported" ? "info" : null}>${rule.source}<//>
          ${rule.subtree ? "this and beneath" : "exactly this OID"}:${" "}
          ${rule.name ? html`<b>${rule.name}</b>` : null}${" "}
          ${rule.severity ? html`<${SeverityChip} band=${band(rule.severity_rank)} />` : null}
          ${editable ? html`<button type="button" class="tap" onClick=${() => this.remove(rule.id)}
            aria-label=${`Withdraw the ${rule.source} rule on ${rule.oid}`}>withdraw</button>` : null}
        </p>`)}
        ${editable && node !== ROOT ? html`<form class="oid-name" onSubmit=${(e) => this.save(e)}>
          <label for="ruleName">Name</label>
          <input id="ruleName" maxlength="80" value=${name} autocomplete="off"
                 onInput=${(e) => this.setState({ name: e.target.value })} />
          <label for="ruleSev">Severity</label>
          <select id="ruleSev" value=${severity}
                  onChange=${(e) => this.setState({ severity: e.target.value })}>
            <option value="">—</option>
            ${GRADES.map((g) => html`<option key=${g} value=${g}>${g}</option>`)}
          </select>
          <label class="oid-exact"><input type="checkbox" checked=${exact}
            onChange=${(e) => this.setState({ exact: e.target.checked })} /> exactly this OID</label>
          <button type="submit" class="primary" disabled=${busy || (!name.trim() && !severity)}
            >Save</button>
        </form>` : null}
        <ul class="oid-children">
          ${tree.children.map((child) => html`<li key=${child.oid}>
            <button type="button" class="oid-child" onClick=${() => onNode(child.oid)}>
              <span class="mono">${child.arc}</span>
              ${child.vendor || child.name ? html`<span>${child.vendor || child.name}</span>` : null}
              <span class="muted">${count(child.classes)}</span>
            </button>
          </li>`)}
        </ul>
        ${tree.more_children
          ? html`<p class="muted">${count(tree.more_children)} more — search instead.</p>` : null}
      ` : null}
    </section>`;
  }
}
