/* One SNMPv3 user, and how to configure equipment to send as it (v0.30.0, ADR #444).
 *
 * The security level decides which fields exist: noAuthNoPriv has a name only, authNoPriv adds an
 * authentication protocol and passphrase, authPriv adds privacy. Passphrases are at least eight
 * characters (RFC 3414) and are never shown back: editing a saved user leaves them blank, and blank
 * keeps the stored key unless the protocol changes.
 *
 * Beneath the form, the lines that make a device send as this user — Cisco IOS and a Net-SNMP test
 * command — built from the choices made here, with the passphrases as placeholders so a secret is
 * never copied into a clipboard or onto a screen by this page.
 */

import { html, Component, cx } from "../../dom.js";
import { PasswordInput } from "../../password.js";

export const LEVELS = ["noAuthNoPriv", "authNoPriv", "authPriv"];
const NETSNMP_PRIV = { DES: "DES", "AES-128": "AES", "AES-192": "AES-192", "AES-256": "AES-256" };
const CISCO_AUTH = { MD5: "md5", SHA: "sha" };
const CISCO_PRIV = { DES: "des", "3DES": "3des", "AES-128": "aes 128", "AES-192-C": "aes 192",
  "AES-256-C": "aes 256" };

/** The device-side configuration for `user`, sending to this appliance. */
export function equipmentLines(user, level, host) {
  const name = user.name || "<user>";
  const flags = ["-v3", `-l ${LEVELS[level]}`, `-u ${name}`];
  if (level >= 1) flags.push(`-a ${user.auth}`, "-A '<auth passphrase>'");
  if (level === 2) flags.push(`-x ${NETSNMP_PRIV[user.priv] || user.priv}`, "-X '<privacy passphrase>'");
  const netsnmp = `snmptrap ${flags.join(" ")} ${host}:162 '' 1.3.6.1.6.3.1.1.5.3`;
  const word = ["noauth", "auth", "priv"][level];
  const security = level === 0 ? "" : ` auth ${CISCO_AUTH[user.auth] || user.auth.toLowerCase()} <auth passphrase>${
    level === 2 ? ` priv ${CISCO_PRIV[user.priv] || user.priv.toLowerCase()} <privacy passphrase>` : ""}`;
  const cisco = [
    `snmp-server group NOC v3 ${word}`,
    `snmp-server user ${name} NOC v3${security}`,
    `snmp-server host ${host} traps version 3 ${word} ${name}`,
    "snmp-server enable traps",
  ].join("\n");
  const ciscoOk = level === 0 || (CISCO_AUTH[user.auth] && (level === 1 || CISCO_PRIV[user.priv]));
  return { netsnmp: NETSNMP_PRIV[user.priv] || level < 2 ? netsnmp : null, cisco: ciscoOk ? cisco : null };
}

export class SnmpUser extends Component {
  constructor(props) {
    super(props);
    const u = props.user || { name: "", auth: "SHA", priv: "AES-128", engine_id: null };
    const level = u.auth === "none" ? 0 : u.priv === "none" ? 1 : 2;
    this.state = { name: u.name, auth: u.auth === "none" ? "SHA" : u.auth,
      priv: u.priv === "none" ? "AES-128" : u.priv, level, engine: u.engine_id || "",
      authPass: "", privPass: "", problem: null, saved: !!(props.user && props.user.saved) };
  }

  done(event) {
    event.preventDefault();
    const { name, auth, priv, level, engine, authPass, privPass, saved } = this.state;
    const prior = this.props.user || {};
    const same = prior.auth === (level >= 1 ? auth : "none") && prior.priv === (level === 2 ? priv : "none");
    // A saved user keeps its stored keys; an unsaved one keeps the passphrases typed a moment ago.
    const keep = same && (saved || !!prior.auth_passphrase);
    let problem = null;
    if (!/^[A-Za-z0-9._@-]{1,32}$/.test(name)) problem = "A name is 1 to 32 letters, digits or . _ @ -";
    else if (level >= 1 && !keep && authPass.length < 8) problem = "The authentication passphrase needs at least 8 characters.";
    else if (level === 2 && !keep && privPass.length < 8) problem = "The privacy passphrase needs at least 8 characters.";
    if (problem) { this.setState({ problem }); return; }
    this.props.onDone({ name, auth: level >= 1 ? auth : "none", priv: level === 2 ? priv : "none",
      engine_id: engine.trim() || null,
      auth_passphrase: authPass || (keep ? prior.auth_passphrase : null) || null,
      priv_passphrase: privPass || (keep ? prior.priv_passphrase : null) || null, saved });
  }

  render({ protocols, onCancel }, { name, auth, priv, level, engine, authPass, privPass, problem, saved }) {
    const host = (globalThis.location && globalThis.location.hostname) || "<appliance>";
    const lines = equipmentLines({ name, auth, priv }, level, host);
    const weak = new Set(protocols.weak || []);
    const keepHint = saved ? "Leave blank to keep the saved one" : "At least 8 characters";
    return html`<form class="snmp-user-form panel-block" onSubmit=${(e) => this.done(e)} autocomplete="off">
      <div class="snmp-user-grid">
        <label class="pe-field"><span>User name</span>
          <input value=${name} maxlength="32" spellcheck=${false} autocapitalize="off"
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></label>
        <div class="pe-field"><span>Security level</span>
          <div class="seg" role="radiogroup" aria-label="Security level">
            ${LEVELS.map((word, i) => html`<button type="button" key=${word} role="radio"
              aria-checked=${level === i} class=${cx("seg-btn", level === i && "on")}
              onClick=${() => this.setState({ level: i })}>${word}</button>`)}
          </div></div>
        ${level >= 1 ? html`<label class="pe-field"><span>Authentication</span>
          <select value=${auth} onChange=${(e) => this.setState({ auth: e.currentTarget.value })}>
            ${protocols.auth.map((p) => html`<option key=${p} value=${p}>${p}${weak.has(p) ? " (weak)" : ""}</option>`)}
          </select></label>
          <div><${PasswordInput} id="snmpAuth" label="Authentication passphrase" autocomplete="new-password"
            value=${authPass} onInput=${(v) => this.setState({ authPass: v })} />
            <p class="hint">${keepHint}.</p></div>` : null}
        ${level === 2 ? html`<label class="pe-field"><span>Privacy</span>
          <select value=${priv} onChange=${(e) => this.setState({ priv: e.currentTarget.value })}>
            ${protocols.priv.map((p) => html`<option key=${p} value=${p}>${p}${weak.has(p) ? " (weak)" : ""}</option>`)}
          </select></label>
          <div><${PasswordInput} id="snmpPriv" label="Privacy passphrase" autocomplete="new-password"
            value=${privPass} onInput=${(v) => this.setState({ privPass: v })} />
            <p class="hint">${keepHint}. AES-192/256 are Net-SNMP's; the “-C” variants are Cisco's.</p></div>` : null}
        <label class="pe-field"><span>Engine ID (optional)</span>
          <input value=${engine} class="mono" placeholder="any device" spellcheck=${false}
            onInput=${(e) => this.setState({ engine: e.currentTarget.value })} /></label>
      </div>
      ${level === 0 ? html`<p class="warnbox">noAuthNoPriv proves nothing about the sender: anyone
        who knows the user name can send as it. Prefer authPriv.</p>` : null}
      <details class="snmp-snippet" open>
        <summary>Configure your equipment</summary>
        ${lines.cisco ? html`<p class="muted">Cisco IOS</p><pre class="token-example">${lines.cisco}</pre>` : null}
        ${lines.netsnmp ? html`<p class="muted">Test from any Linux host (Net-SNMP)</p>
          <pre class="token-example">${lines.netsnmp}</pre>` : null}
      </details>
      ${problem ? html`<p class="err" role="alert">${problem}</p>` : null}
      <div class="roles-foot">
        <button type="submit" class="primary">${saved ? "Keep changes" : "Add user"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
        <span class="muted">Nothing is stored until you save the SNMP settings.</span>
      </div>
    </form>`;
  }
}
