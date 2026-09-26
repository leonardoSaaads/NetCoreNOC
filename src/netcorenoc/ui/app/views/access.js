/* People & access: users, roles, service tokens and visibility on one screen (v0.25.0, ADR #403).
 *
 * Three administer screens — Users, Service tokens, Governance — answered one question ("who may
 * do what?") from three places, and the answer to "what may Ana do?" needed all three. They are
 * now tabs of one screen, and a person's access is edited where the person is:
 *
 *   * **People** — the accounts, and one editor per person: photo, name, role, and the capability
 *     grid that role checks (uncheck to give this person less);
 *   * **Roles** — what every person of a role holds by default, in the same grid;
 *   * **Service tokens** — credentials for programs, each with a purpose and its own access;
 *   * **Visibility** — which network elements viewers and editors see.
 *
 * Each tab reads only what it shows, and only if the session holds the capability to read it; the
 * tab is in the address (`?tab=`), so a link can open it.
 */

import { html, Component, cx } from "../dom.js";
import { get } from "../api.js";
import { Loading, Failed } from "../widgets.js";
import { can } from "../session.js";
import { PeopleList, PersonEditor } from "./parts/people.js";
import { RolesPanel } from "./parts/roles.js";
import { TokensPanel } from "./parts/tokenspanel.js";
import { VisibilityPanel } from "./parts/visibility.js";

const TABS = [
  ["people", "People", "users.manage"],
  ["roles", "Roles", "rbac.read"],
  ["tokens", "Service tokens", "tokens.manage"],
  ["visibility", "Visibility", "scope.read"],
];

export class Access extends Component {
  constructor(props) {
    super(props);
    this.state = { data: {}, error: null, editing: null };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (String(previous.query || "") !== String(this.props.query || "")) this.read();
  }

  tab() {
    const asked = (this.props.query && this.props.query.get("tab")) || this.props.tab || "people";
    const allowed = TABS.filter(([, , cap]) => can(cap)).map(([key]) => key);
    return allowed.includes(asked) ? asked : allowed[0];
  }

  /** What the open tab needs, and only what the session may read. */
  async read() {
    const tab = this.tab();
    const wants = {
      people: [["users", "/api/users"], ["rbac", "/api/rbac"]],
      roles: [["rbac", "/api/rbac"]],
      tokens: [["tokens", "/api/tokens"], ["rbac", "/api/rbac"]],
      visibility: [["scope", "/api/scope"]],
    }[tab] || [];
    const need = { users: "users.manage", rbac: "rbac.read", tokens: "tokens.manage", scope: "scope.read" };
    try {
      const pairs = await Promise.all(wants.filter(([key]) => can(need[key]))
        .map(async ([key, path]) => [key, await get(path)]));
      this.setState({ data: { ...this.state.data, ...Object.fromEntries(pairs) }, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  go(tab) {
    this.setState({ editing: null });
    this.props.navigate(`#/access?tab=${tab}`);
  }

  saved() {
    this.setState({ editing: null });
    this.read();
  }

  body(tab, { users, rbac, tokens, scope }) {
    if (tab === "people") {
      if (!users) return html`<${Loading} label="Reading accounts" />`;
      const { editing } = this.state;
      if (editing != null) {
        const user = editing === "new" ? null : users.find((u) => u.id === editing);
        return html`<${PersonEditor} key=${editing} user=${user} rbac=${rbac}
          onClose=${() => this.setState({ editing: null })} onSaved=${() => this.saved()} />`;
      }
      return html`<${PeopleList} users=${users} rbac=${rbac}
        onOpen=${(id) => this.setState({ editing: id })} />`;
    }
    if (tab === "roles") {
      return rbac ? html`<${RolesPanel} rbac=${rbac} onChanged=${() => this.read()} />`
        : html`<${Loading} label="Reading roles" />`;
    }
    if (tab === "tokens") {
      return tokens ? html`<${TokensPanel} tokens=${tokens} rbac=${rbac} onChanged=${() => this.read()} />`
        : html`<${Loading} label="Reading tokens" />`;
    }
    return scope ? html`<${VisibilityPanel} key=${scope.active ? scope.active.id : 0} scope=${scope}
      onChanged=${() => this.read()} />` : html`<${Loading} label="Reading visibility" />`;
  }

  render(_props, { data, error }) {
    const tab = this.tab();
    return html`<div class="access">
      <div class="access-tabs" role="tablist" aria-label="People and access">
        ${TABS.filter(([, , cap]) => can(cap)).map(([key, label]) => html`<button type="button"
          key=${key} role="tab" aria-selected=${tab === key} class=${cx("access-tab", tab === key && "on")}
          onClick=${() => this.go(key)}>${label}</button>`)}
      </div>
      ${error ? html`<${Failed} error=${error} retry=${() => this.read()} what="this tab" />`
        : this.body(tab, data)}
    </div>`;
  }
}
