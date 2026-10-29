/* The vault's shared marks (ADR #471): what each kind of credential is, drawn and said once.
 *
 * A person adding a credential should know what to pick before reading a label: SSH is a prompt,
 * a key is a key, SNMP a device answering in waves, an API its brackets, SFTP two arrows, SMTP an
 * envelope — and an AI provider its own logo (`brands.js`). Each kind also says, in one sentence,
 * what it is for, and the access levels say what each lets its holder do. The catalogue the server
 * sends stays the authority on fields, ports and versions; this adds only the words and the marks.
 */

import { html, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { BrandMark } from "../../brands.js";

/** Each device kind's mark, its family (the list's filter) and what it is for. */
export const KINDS = {
  ssh_password: { icon: "terminal", family: "ssh", help: "Sign in to a router or a switch with a username and a password." },
  ssh_key: { icon: "key", family: "ssh", help: "Sign in with a private key (PEM or OpenSSH): no password on the wire." },
  sftp: { icon: "transfer", family: "ssh", help: "Copy files to or from a device: backups, firmware, logs." },
  snmp_community: { icon: "signal", family: "snmp", help: "Read counters and state with an SNMP v1/v2c community string." },
  snmp_v3: { icon: "signal", family: "snmp", help: "SNMP with a user, authentication and privacy (encryption)." },
  api_token: { icon: "code", family: "api", help: "A REST API's token: a controller, an orchestrator, a ticketing tool." },
  smtp: { icon: "mail", family: "smtp", help: "Send email through a mail server: a username and a password." },
  other: { icon: "more", family: "other", help: "Anything else: a web console, a jump host, a vendor portal." },
};

/** The list's filter: each family of device credential, with its mark. */
export const FAMILIES = [
  ["ssh", "SSH & SFTP", "terminal"], ["snmp", "SNMP", "signal"], ["api", "API", "code"],
  ["smtp", "SMTP", "mail"], ["other", "Other", "more"],
];

/** What each access level lets its holder do on the device, with a mark. */
export const ACCESS = {
  view_only: { icon: "eye", short: "View only" },
  diagnostic: { icon: "search", short: "Diagnostic" },
  operator: { icon: "wrench", short: "Operator" },
  root: { icon: "crown", short: "Root / admin" },
};

/** The provider an AI kind's key is for (`ai_<provider>`), or null for a device credential. */
export function providerOf(kindId) {
  return kindId && kindId.startsWith("ai_") ? kindId.slice(3) : null;
}

/** A credential's mark: its provider's logo, or its kind's icon on a tile. */
export function KindMark({ kind, size = 28 }) {
  const provider = providerOf(kind);
  if (provider) return html`<${BrandMark} brand=${provider} size=${size} />`;
  const meta = KINDS[kind] || KINDS.other;
  return html`<span class=${cx("kind-tile", `kind-${meta.family}`)} aria-hidden="true"
    style=${`width:${size}px;height:${size}px;font-size:${Math.round(size * 0.58)}px`}>
    <${Icon} name=${meta.icon} />
  </span>`;
}

/** The access levels as cards to choose from: the mark, the level, what it allows. */
export function AccessCards({ levels, value, onChange, name }) {
  return html`<div class="access-cards" role="radiogroup" aria-label="Access it grants">
    ${levels.map((a) => {
      const meta = ACCESS[a.id] || { icon: "info", short: a.label };
      const [, what] = a.label.split(" — ");
      return html`<label key=${a.id} class=${cx("access-card", `access-card-${a.id}`, value === a.id && "on")}>
        <input type="radio" name=${name} value=${a.id} checked=${value === a.id}
          onChange=${() => onChange(a.id)} />
        <span class="access-card-icon"><${Icon} name=${meta.icon} /></span>
        <span class="access-card-text"><b>${meta.short}</b><span>${what || ""}</span></span>
      </label>`;
    })}
  </div>`;
}

/** A switch: a button that says what it is and whether it is on, never the colour alone. */
export function Toggle({ on, label, onToggle, disabled, hint }) {
  return html`<button type="button" role="switch" aria-checked=${on ? "true" : "false"}
      class=${cx("toggle", on && "on")} disabled=${disabled} title=${hint || label} onClick=${onToggle}>
    <span class="toggle-track" aria-hidden="true"><span class="toggle-knob"></span></span>
    <span class="toggle-label">${label}</span>
  </button>`;
}
