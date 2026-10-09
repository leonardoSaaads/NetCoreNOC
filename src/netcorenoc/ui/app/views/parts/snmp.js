/* Settings → SNMP: what the trap receiver accepts (v0.30.0, ADR #444).
 *
 * The default needs nothing: every SNMPv1 and v2c trap is accepted, whatever its community — the
 * appliance's zero-configuration promise. This tab is where an admin narrows that: which versions,
 * which communities, and the SNMPv3 users whose traps are authenticated (and decrypted). A save
 * reaches the running receiver at once; nothing restarts.
 *
 * Secrets go one way. A community or a passphrase is typed, sent once, and never shown again: the
 * server keeps a keyed hash of a community and the RFC 3414 master key of a passphrase. A user saved
 * without retyping its passphrases keeps its keys while its protocols stay the same.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { Loading, Failed, SectionHeading } from "../../widgets.js";
import { InfoTip } from "../../info.js";
import { can } from "../../session.js";
import { SnmpUser, LEVELS } from "./snmpuser.js";

/** Why the receiver refused a datagram, in words, keyed by the quarantine reason. */
const REASONS = {
  "community-not-accepted": "v1/v2c trap with a community that is not on the list",
  "snmp-v1-not-accepted": "SNMPv1 is switched off", "snmp-v2c-not-accepted": "SNMPv2c is switched off",
  "snmp-v3-not-accepted": "SNMPv3 is switched off",
  "v3-unknown-user": "SNMPv3 user not configured here",
  "v3-authentication-failed": "wrong authentication passphrase or protocol",
  "v3-decryption-failed": "wrong privacy passphrase or protocol",
  "v3-not-in-time-window": "replayed, or the device's engine clock went backwards",
  "v3-unknown-engine-id": "the user is pinned to another engine ID",
  "v3-security-level-too-low": "the device sent less security than the user requires",
  "v3-unsupported-security-level": "the device sent more security than the user has keys for",
  "v3-privacy-unavailable": "encrypted trap, and privacy support is not installed",
};

function draft(data) {
  return {
    v1: data.v1, v2c: data.v2c, v3: data.v3, time_window: data.time_window,
    any: data.communities.any, keep: data.communities.entries.map((e) => e.id), add: [],
    users: data.users.map((u) => ({ ...u, saved: true })),
  };
}

export class SnmpPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, form: null, busy: false, outcome: null, community: "",
      editing: null };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      const data = await get("/api/snmp");
      this.setState({ data, form: draft(data), error: null, editing: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  set(patch) { this.setState({ form: { ...this.state.form, ...patch }, outcome: null }); }

  async save() {
    const { form } = this.state;
    this.setState({ busy: true, outcome: null });
    try {
      await post("/api/snmp", {
        v1: form.v1, v2c: form.v2c, v3: form.v3, time_window: form.time_window,
        communities: { any: form.any, keep: form.keep, add: form.add },
        users: form.users.map(({ name, auth, priv, engine_id: engine, auth_passphrase: a,
          priv_passphrase: p }) => ({ name, auth, priv, engine_id: engine || null,
          auth_passphrase: a || null, priv_passphrase: p || null })),
      });
      this.setState({ busy: false, outcome: { ok: true } });
      await this.load();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, text: error.detail || error.message } });
    }
  }

  addCommunity(event) {
    event.preventDefault();
    const value = this.state.community;
    if (!value || this.state.form.add.includes(value)) return;
    this.setState({ community: "" });
    this.set({ add: [...this.state.form.add, value] });
  }

  render(_props, { data, error, form, busy, outcome, community, editing }) {
    if (error) return html`<${Failed} error=${error} retry=${() => this.load()} what="the SNMP settings" />`;
    if (!data) return html`<${Loading} label="Reading the SNMP settings" />`;
    const writable = can("config.write");
    const hints = Object.fromEntries(data.communities.entries.map((e) => [e.id, e.hint]));
    const refusals = Object.entries(data.refusals || {}).sort((a, b) => b[1] - a[1]);
    return html`<div class="settingsview snmp">
      <div class="settings-intro"><p><b>What the trap receiver accepts.</b> Out of the box every
        SNMPv1 and v2c trap is accepted, whatever its community, and SNMPv3 traps from the users
        listed here. Changes apply at once — no restart.</p></div>
      ${data.privacy_available ? null : html`<p class="warnbox" role="note"><b>SNMPv3 privacy is not
        available on this install.</b> Encrypted (authPriv) traps are quarantined until the optional
        package is installed: <code class="mono">pip install "netcorenoc[snmpv3]"</code>. The Docker
        image includes it.</p>`}

      <section class="panel-block">
        <${SectionHeading} title="Versions" hint="Switch off a version you do not use, and its traps are refused." />
        <div class="snmp-versions">
          ${[["v1", "SNMPv1", "Oldest equipment; community only."],
             ["v2c", "SNMPv2c", "The common default; community only."],
             ["v3", "SNMPv3", "Authenticated and, optionally, encrypted."]].map(([key, label, note]) => html`
            <label key=${key} class=${cx("snmp-version", form[key] && "on")}>
              <input type="checkbox" checked=${form[key]} disabled=${!writable}
                onChange=${(e) => this.set({ [key]: e.currentTarget.checked })} />
              <span><b>${label}</b><span class="muted">${note}</span></span>
            </label>`)}
        </div>
      </section>

      <section class="panel-block">
        <${SectionHeading} title="Communities (v1 and v2c)"
          hint="A community is a shared word, sent in clear text. Accepting any is the zero-configuration default." />
        <div class="seg" role="radiogroup" aria-label="Communities">
          ${[[true, "Accept any community"], [false, "Only these communities"]].map(([any, label]) => html`
            <button type="button" key=${label} role="radio" aria-checked=${form.any === any}
              class=${cx("seg-btn", form.any === any && "on")} disabled=${!writable}
              onClick=${() => this.set({ any })}>${label}</button>`)}
        </div>
        ${form.any ? null : html`<div class="snmp-communities">
          <ul class="chip-list">
            ${form.keep.map((id) => html`<li key=${id} class="chip-static">${hints[id]}
              ${writable ? html`<button type="button" class="chip-x" aria-label="Remove this community"
                onClick=${() => this.set({ keep: form.keep.filter((k) => k !== id) })}>×</button>` : null}</li>`)}
            ${form.add.map((c) => html`<li key=${`new-${c}`} class="chip-static chip-new">${`new · ${c.length} chars`}
              <button type="button" class="chip-x" aria-label="Remove this community"
                onClick=${() => this.set({ add: form.add.filter((x) => x !== c) })}>×</button></li>`)}
          </ul>
          ${writable ? html`<form class="inline-add" onSubmit=${(e) => this.addCommunity(e)}>
            <input type="password" value=${community} maxlength="64" placeholder="community"
              aria-label="Add a community" autocomplete="off"
              onInput=${(e) => this.setState({ community: e.currentTarget.value })} />
            <button type="submit" disabled=${!community}>Add</button>
          </form>` : null}
          <p class="hint">Stored as a keyed hash: once saved, a community is shown only by its first
            letter and length.</p>
        </div>`}
      </section>

      <section class="panel-block">
        <${SectionHeading} title="SNMPv3 users"
          hint="A user is accepted from any device configured with it; pin an engine ID to accept one device only." />
        ${form.users.length ? html`<ul class="snmp-users">${form.users.map((u, i) => html`
          <li key=${u.name || i} class="snmp-user-row">
            <b class="mono">${u.name}</b>
            <span class="badge">${LEVELS[u.auth === "none" ? 0 : u.priv === "none" ? 1 : 2]}</span>
            <span class="muted">${u.auth === "none" ? "no auth" : u.auth}${u.priv === "none" ? "" : ` · ${u.priv}`}</span>
            <span class="muted mono">${u.engine_id ? `engine ${u.engine_id}` : "any engine"}</span>
            ${writable ? html`<span class="token-acts">
              <button type="button" class="linkish" onClick=${() => this.setState({ editing: i })}>Edit</button>
              <button type="button" class="linkish" onClick=${() => this.set({ users: form.users.filter((_, k) => k !== i) })}>Remove</button>
            </span>` : null}
          </li>`)}</ul>` : html`<p class="muted">No SNMPv3 user yet: v3 traps are refused as
            “unknown user” until one is added.</p>`}
        ${editing !== null ? html`<${SnmpUser} key=${editing} user=${form.users[editing] || null}
          protocols=${data.protocols} onCancel=${() => this.setState({ editing: null })}
          onDone=${(user) => {
            const users = [...form.users];
            if (editing < users.length) users[editing] = user; else users.push(user);
            this.setState({ editing: null });
            this.set({ users });
          }} />` : writable ? html`<button type="button" onClick=${() => this.setState({ editing: form.users.length })}>
            Add a user</button>` : null}
      </section>

      <section class="panel-block">
        <label class="check-row"><input type="checkbox" checked=${form.time_window} disabled=${!writable}
          onChange=${(e) => this.set({ time_window: e.currentTarget.checked })} />
          <span><b>Replay protection</b> — refuse an SNMPv3 trap more than 150 s older than the
            sender's clock (RFC 3414).${" "}<${InfoTip} label="When to switch it off">A device whose
            engine boots counter does not survive a reboot looks like a replay after it restarts.
            Switch this off for such equipment, or fix its engine boots persistence.<//></span></label>
      </section>

      ${refusals.length ? html`<section class="panel-block">
        <${SectionHeading} title="Refused since the appliance started"
          hint="Each refused datagram is kept in Quarantine with its source." />
        <ul class="mini-list">${refusals.map(([reason, n]) => html`<li key=${reason}>
          <b class="mono">${n}</b><span>${REASONS[reason] || reason}</span>
          <span class="muted mono">${reason}</span></li>`)}</ul>
        <a href="#/quarantine">Open Quarantine</a>
      </section>` : null}

      ${writable ? html`<div class="roles-foot save-bar">
        <button type="button" class="primary" disabled=${busy} onClick=${() => this.save()}>
          ${busy ? "Saving…" : "Save SNMP settings"}</button>
        <button type="button" disabled=${busy} onClick=${() => this.setState({ form: draft(data), outcome: null })}>Discard</button>
        ${outcome && outcome.ok ? html`<span class="ok-note" role="status">Saved — the receiver uses it now.</span>` : null}
        ${outcome && !outcome.ok ? html`<span class="err" role="alert">${outcome.text}</span>` : null}
      </div>` : null}
    </div>`;
  }
}
