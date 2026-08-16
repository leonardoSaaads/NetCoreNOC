/* Service tokens: non-interactive credentials, shown once.
 *
 * The "shown once" is the whole security property and the screen is built around it. The value is
 * displayed in a block the operator has to dismiss deliberately, with the reason stated — a token
 * that scrolled away with the rest of a table would be a token someone re-issues rather than
 * copies, and re-issuing leaves a revoked credential behind every time.
 */

import { html, Component } from "../dom.js";
import { get, post, del } from "../api.js";
import { Loader, Empty, DataTable, SectionHeading, cell } from "../widgets.js";
import { plural } from "../format.js";
import { Destructive } from "../destructive.js";

const ROLES = ["viewer", "editor", "admin"];

export class Tokens extends Loader {
  constructor(props) {
    super(props);
    this.what = "the token list";
    this.loadingLabel = "Reading service tokens";
    this.state = { ...this.state, issued: null };
  }

  async load() { return get("/api/tokens"); }

  view(tokens) {
    const columns = [
      { key: "name", label: "name" },
      { key: "role", label: "role" },
      { key: "state", label: "state" },
      { key: "actions", label: "" },
    ];
    const rows = tokens.map((token) => ({
      key: token.id,
      tone: token.revoked ? "quiet" : null,
      cells: {
        name: token.name,
        role: html`<span class="role-tag">${token.role}</span>`,
        state: token.revoked ? "revoked" : "active",
        actions: cell(html`<td>${token.revoked ? "" : html`<${Destructive}
          title=${`Revoke ${token.name}`}
          hint="The credential stops working immediately."
          previewLabel="Revoke…"
          confirmLabel=${`Revoke ${token.name}`}
          consequence=${`Anything using the "${token.name}" token stops being able to reach the ` +
                        `API the moment this is applied. The token value cannot be recovered, so ` +
                        `a replacement has to be issued and distributed.`}
          preview=${async () => ({
            measured: false,
            message: "Revocation has no count to preview: it affects exactly this credential. " +
                     "The appliance cannot tell you what is currently using it.",
          })}
          apply=${async () => del(`/api/tokens/${token.id}`)}
          onDone=${this.reload} />`}</td>`),
      },
    }));

    return html`<div class="tokensview">
      <p class="hint">${plural(tokens.length, "token")}. A token carries a role exactly as a user
        account does, and Governance narrows it exactly as it narrows a user's.</p>
      ${this.state.issued ? html`<div class="tokval" role="alert">
        <b>Copy this now — it is shown once and is not recoverable.</b>
        <code class="mono token-value">${this.state.issued}</code>
        <p class="hint">The appliance stores a hash. Nobody, including an administrator, can read
          this value back.</p>
        <button type="button" onClick=${() => this.setState({ issued: null })}>
          I have copied it
        </button>
      </div>` : null}
      ${tokens.length ? html`<${DataTable} columns=${columns} rows=${rows} />`
        : html`<${Empty} title="No service tokens."
                 will="Tokens you issue appear here, by name and role — never by value."
                 meanwhile="Interactive sign-in needs no token; these are for scripts and collectors." />`}
      <${NewToken} onIssued=${(value) => { this.setState({ issued: value }); this.reload(); }} />
    </div>`;
  }
}

class NewToken extends Component {
  constructor(props) {
    super(props);
    this.state = { name: "", role: "viewer", busy: false, error: null };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      const out = await post("/api/tokens", { name: this.state.name, role: this.state.role });
      this.setState({ name: "", busy: false });
      this.props.onIssued(out.token);
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render(_props, { name, role, busy, error }) {
    return html`<section class="panel-block">
      <${SectionHeading} title="Issue a token"
        hint="The value is returned once, in the response to this form, and never again." />
      <form class="row-form" onSubmit=${this.submit} autocomplete="off">
        <label class="visually-hidden" for="tn">Token name</label>
        <input id="tn" placeholder="token name" value=${name}
               onInput=${(e) => this.setState({ name: e.target.value })} />
        <label class="visually-hidden" for="tr">Role</label>
        <select id="tr" value=${role} onChange=${(e) => this.setState({ role: e.target.value })}>
          ${ROLES.map((r) => html`<option key=${r} value=${r}>${r}</option>`)}
        </select>
        <button type="submit" disabled=${busy}>${busy ? "Issuing…" : "Create token"}</button>
      </form>
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}
