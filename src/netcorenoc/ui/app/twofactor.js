/* Two-factor sign-in, on screen (ADR #482): the QR code, the code field and the recovery codes.
 *
 * Used by the sign-in's second step (`SecondStep`, beside the password card) and by Your account
 * (new recovery codes). The server decides everything; this draws what it sent:
 *
 *   * **The QR code** arrives as rows of `0`/`1` (`crosscutting/qrcode.py`) and is drawn here as
 *     one inline SVG path — no image, no data URL, no third-party script near the secret. It is
 *     always dark on white, whatever the theme: a phone camera reads that best.
 *   * **The code field** is one real `<input>` (one-time-code autofill, numeric keypad, paste)
 *     drawn as six boxes behind it, so it reads like the app it is copied from.
 *   * **Recovery codes** can be copied or saved as a text file, and the person says they kept
 *     them before the first code is accepted: the screen that shows them is the only one that will.
 */

import { html, Component, cx } from "./dom.js";
import { post, get, ApiError } from "./api.js";
import { Icon } from "./icons.js";

/** The QR code as one SVG path, with the four-module quiet zone the standard asks for. */
export function QrCode({ rows, label }) {
  const size = rows.length;
  const quiet = 4;
  let d = "";
  rows.forEach((row, y) => {
    let x = 0;
    while (x < size) {
      if (row[x] !== "1") { x += 1; continue; }
      const start = x;
      while (x < size && row[x] === "1") x += 1;
      d += `M${start + quiet} ${y + quiet}h${x - start}v1h${start - x}z`;
    }
  });
  const box = size + quiet * 2;
  return html`<svg class="qr-code" viewBox=${`0 0 ${box} ${box}`} role="img" aria-label=${label}
    shape-rendering="crispEdges"><rect width=${box} height=${box} fill="#fff" />
    <path d=${d} fill="#0b0d12" /></svg>`;
}

/** Six boxes over one real input. `onDone(code)` fires when the sixth digit arrives. */
export class CodeInput extends Component {
  componentDidMount() {
    if (this.props.autoFocus && this.input) this.input.focus();
  }

  change(raw) {
    const { recovery, onInput, onDone } = this.props;
    const value = recovery ? raw.slice(0, 11) : raw.replace(/\D/g, "").slice(0, 6);
    onInput(value);
    if (!recovery && value.length === 6 && onDone) onDone(value);
  }

  render({ id, value, recovery, disabled, describedBy }) {
    if (recovery) {
      return html`<input id=${id} class="code-recovery mono" value=${value} disabled=${disabled}
        autocomplete="off" autocapitalize="none" spellcheck=${false} placeholder="xxxxx-xxxxx"
        aria-describedby=${describedBy} ref=${(el) => { this.input = el; }}
        onInput=${(e) => this.change(e.currentTarget.value)} />`;
    }
    const digits = value.split("");
    return html`<div class=${cx("code-boxes", disabled && "is-disabled")}>
      <input id=${id} class="code-real" value=${value} disabled=${disabled} inputmode="numeric"
        autocomplete="one-time-code" maxlength="6" pattern="[0-9]*" aria-describedby=${describedBy}
        ref=${(el) => { this.input = el; }} onInput=${(e) => this.change(e.currentTarget.value)} />
      ${[0, 1, 2, 3, 4, 5].map((i) => html`<span key=${i} aria-hidden="true"
        class=${cx("code-box", i === digits.length && "is-next", digits[i] && "is-filled")}>${digits[i] || ""}</span>`)}
    </div>`;
  }
}

async function copyText(text) {
  try { await globalThis.navigator.clipboard.writeText(text); return true; } catch { return false; }
}

function saveText(name, text) {
  const blob = new globalThis.Blob([text], { type: "text/plain" });
  const url = globalThis.URL.createObjectURL(blob);
  const anchor = globalThis.document.createElement("a");
  anchor.setAttribute("href", url);
  anchor.setAttribute("download", name);
  anchor.click();
  globalThis.URL.revokeObjectURL(url);
}

/** How a hidden value is drawn: its shape, never its characters. */
function masked(text) { return text.replace(/[^ -]/g, "•"); }

/* The three states a secret on screen goes through (asked for by the owner, ADR #482):
 * `hidden` — nothing drawn but its count and the buttons that need no eyes (Copy, Save);
 * `masked` — its shape in dots, a warning that anyone who can see the screen can use it, and
 * "I'm ready — show them"; `shown` — the characters, with Hide. Copy and Save work in every state,
 * so a person in a public place never has to put a code on the screen at all. */

/** The recovery codes: copied or saved without being shown; shown only on purpose. */
export class RecoveryCodes extends Component {
  constructor(props) {
    super(props);
    this.state = { copied: false, reveal: "hidden" };
  }

  text() {
    const { codes, account } = this.props;
    const lines = codes.map((c) => c.toUpperCase()).join("\n");
    return `NetCoreNOC recovery codes for ${account}\nEach works once, instead of an authenticator code.\n\n${lines}\n`;
  }

  async copy() {
    this.setState({ copied: await copyText(this.text()) });
  }

  render({ codes, account }, { copied, reveal }) {
    const step = (next) => this.setState({ reveal: next });
    return html`<div class=${cx("recovery-box", `is-${reveal}`)}>
      ${reveal === "hidden" ? html`<p class="recovery-hidden"><${Icon} name="lock" />
        <span><b>${codes.length} codes, hidden.</b> Copy or save them without showing them on screen.</span></p>`
        : html`<ol class="recovery-codes mono" aria-label="Recovery codes">
            ${codes.map((c) => html`<li key=${c}>${reveal === "shown" ? c : masked(c)}</li>`)}
          </ol>`}
      ${reveal === "masked" ? html`<p class="recovery-warn" role="alert"><${Icon} name="warn" />
        Anyone who can see your screen can use these codes. Check no one is looking.</p>` : null}
      <div class="recovery-actions">
        <button type="button" class="btn-ghost" onClick=${() => this.copy()}>
          <${Icon} name=${copied ? "check" : "copy"} /> ${copied ? "Copied" : "Copy"}</button>
        <button type="button" class="btn-ghost" onClick=${() => saveText(`netcorenoc-recovery-${account}.txt`, this.text())}>
          <${Icon} name="download" /> Save .txt</button>
        ${reveal === "hidden" ? html`<button type="button" class="btn-ghost recovery-show" onClick=${() => step("masked")}>
            <${Icon} name="eye" /> Show codes</button>`
          : reveal === "masked" ? html`<button type="button" class="recovery-ready" onClick=${() => step("shown")}>
            I'm ready — show them</button>`
          : html`<button type="button" class="btn-ghost" onClick=${() => step("hidden")}>
            <${Icon} name="eye-off" /> Hide</button>`}
      </div>
    </div>`;
  }
}

/** The secret as text, for an app that cannot scan — under the same three states. */
class ManualKey extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, copied: false, reveal: "masked" };
  }

  render({ secret }, { open, copied, reveal }) {
    const step = (next) => this.setState({ reveal: next });
    if (!open) {
      return html`<button type="button" class="linkish tf-manual" onClick=${() => this.setState({ open: true })}>
        Can't scan? Type a key instead</button>`;
    }
    return html`<div class="tf-key">
      <code class="mono" aria-label="Setup key">${reveal === "shown" ? secret : masked(secret)}</code>
      <button type="button" class="btn-ghost" aria-label="Copy the setup key"
        onClick=${async () => this.setState({ copied: await copyText(secret.replace(/ /g, "")) })}>
        <${Icon} name=${copied ? "check" : "copy"} /></button>
      ${reveal === "shown"
        ? html`<button type="button" class="btn-ghost" aria-label="Hide the setup key" onClick=${() => step("masked")}>
            <${Icon} name="eye-off" /></button>`
        : html`<button type="button" class="linkish" onClick=${() => step("shown")}>I'm ready — show it</button>`}
    </div>`;
  }
}

/** The panel beside the password card. `step` is `POST /api/login`'s answer. */
export class SecondStep extends Component {
  constructor(props) {
    super(props);
    this.state = { code: "", busy: false, error: "", saved: false, recovery: false };
  }

  async submit(event, given) {
    if (event) event.preventDefault();
    const { step, onSignedIn, onRestart } = this.props;
    const enrolling = step.second_factor === "enroll";
    const code = (given ?? this.state.code).trim();
    if (this.state.busy) return;
    if (enrolling && !this.state.saved) {
      this.setState({ error: "Keep your recovery codes first, then tick the box." });
      return;
    }
    if (!code) { this.setState({ error: "Enter the code from your authenticator app." }); return; }
    this.setState({ busy: true, error: "" });
    try {
      const out = await post("/api/login/code", { challenge: step.challenge, code });
      const me = await get("/api/me");
      onSignedIn({ ...me, user: me.user ?? out.user, role: me.role ?? out.role });
    } catch (error) {
      if (error instanceof ApiError && error.status === 410) {
        onRestart(error.detail || "This sign-in has expired. Enter your password again.");
        return;
      }
      this.setState({ busy: false, code: "", error: error instanceof ApiError && error.status === 429
        ? "Too many wrong codes. Wait a moment and try again."
        : error.detail || "That code was not accepted." });
    }
  }

  render({ step, onRestart }, { code, busy, error, saved, recovery }) {
    const enrol = step.enrollment;
    const enrolling = step.second_factor === "enroll";
    return html`<form class=${cx("tf-panel", enrolling && "is-enrol")} onSubmit=${(e) => this.submit(e)}
      aria-labelledby="tf-title" autocomplete="off">
      <header class="tf-head">
        <span class="tf-badge"><${Icon} name=${enrolling ? "smartphone" : "shield"} /></span>
        <div>
          <h2 id="tf-title">${enrolling ? "Set up two-factor sign-in" : "Two-factor code"}</h2>
          <p class="hint">${enrolling
            ? "Required for every account. Use Google Authenticator, Authy, 1Password or any authenticator app."
            : recovery ? "Enter one of the recovery codes you saved." : "Open your authenticator app and enter the 6-digit code."}</p>
        </div>
      </header>
      ${enrolling ? html`
        <ol class="tf-steps">
          <li class="tf-step">
            <span class="tf-num" aria-hidden="true">1</span>
            <div class="tf-step-body">
              <b>Scan this QR code</b>
              <div class="tf-qr-row">
                <${QrCode} rows=${enrol.qr} label=${`QR code for ${enrol.issuer}: ${enrol.account}`} />
                <div class="tf-qr-side">
                  <p class="muted">In your app, choose <i>Add account</i> and point the camera here.</p>
                  <${ManualKey} secret=${enrol.secret} />
                </div>
              </div>
            </div>
          </li>
          <li class="tf-step">
            <span class="tf-num" aria-hidden="true">2</span>
            <div class="tf-step-body">
              <b>Keep your recovery codes</b>
              <p class="muted">If you lose your phone, each code signs you in once. This is the only time you can copy them.</p>
              <${RecoveryCodes} codes=${enrol.recovery_codes} account=${enrol.account} />
              <label class="check-row tf-saved"><input type="checkbox" checked=${saved}
                onChange=${(e) => this.setState({ saved: e.currentTarget.checked, error: "" })} />
                I saved my recovery codes</label>
            </div>
          </li>
          <li class="tf-step">
            <span class="tf-num" aria-hidden="true">3</span>
            <div class="tf-step-body">
              <label for="tfCode"><b>Enter the 6-digit code</b></label>
              <${CodeInput} id="tfCode" value=${code} disabled=${busy || !saved}
                onInput=${(v) => this.setState({ code: v, error: "" })}
                onDone=${(v) => this.submit(null, v)} />
            </div>
          </li>
        </ol>` : html`
        <div class="tf-verify">
          <label for="tfCode" class="visually-hidden">${recovery ? "Recovery code" : "Authenticator code"}</label>
          <${CodeInput} id="tfCode" value=${code} disabled=${busy} recovery=${recovery} autoFocus=${true}
            onInput=${(v) => this.setState({ code: v, error: "" })}
            onDone=${(v) => this.submit(null, v)} />
          <button type="button" class="linkish" onClick=${() => this.setState({ recovery: !recovery, code: "", error: "" })}>
            ${recovery ? "Use the authenticator app instead" : "Lost your phone? Use a recovery code"}</button>
        </div>`}
      ${error ? html`<p class="err" role="alert" id="tfErr">${error}</p>` : null}
      <div class="tf-actions">
        <button type="button" class="btn-ghost" onClick=${() => onRestart("")}>Back</button>
        <button type="submit" class="primary" id="tfBtn" disabled=${busy || (enrolling && !saved)}>
          ${busy ? "Checking…" : enrolling ? "Turn on and sign in" : "Verify and sign in"}</button>
      </div>
    </form>`;
  }
}
