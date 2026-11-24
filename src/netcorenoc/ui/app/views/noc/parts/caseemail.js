/* A case's email, written and read on its feed (ADR #500).
 *
 * **Send email** opens a small editor: To, Cc and Bcc, a subject that keeps the case's tag, and a
 * body with bold, italic, underline, a size, lists and links. What is typed is sent as a document
 * (`engine/noc/richtext.py`): the editor's markup is walked here into paragraphs, lists and runs,
 * and the server writes the email's HTML from them — no markup a browser produced is relayed, and
 * none is ever put back into the page (the console has no `innerHTML`, ADR #175).
 *
 * An email on the feed shows who it is from and to, its subject and its body; **Reply** and
 * **Reply all** ask the server for the draft — whom it goes to, `Re:` once, the original quoted —
 * so nobody types the addresses again.
 */

import { html, Component, cx } from "../../../dom.js";
import { get, post } from "../../../api.js";
import { Icon } from "../../../icons.js";

const SIZES = [["", "Normal"], ["small", "Small"], ["large", "Large"], ["huge", "Huge"]];
/** `document.execCommand("fontSize")` writes `<font size="1..7">`; what each reads as. */
const FONT_SIZE = { 1: "small", 2: "small", 3: "", 4: "large", 5: "large", 6: "huge", 7: "huge" };
const COMMAND_SIZE = { small: "2", "": "3", large: "5", huge: "6" };

/* ---------- the document, read from the editor and drawn from the server ---------- */

function runsOf(node, marks, out) {
  if (node.nodeType === 3) {
    if (node.nodeValue) out.push({ t: node.nodeValue, ...marks });
    return;
  }
  if (node.nodeType !== 1) return;
  const tag = node.nodeName;
  if (tag === "BR") { out.push({ t: "\n", ...marks }); return; }
  const next = { ...marks };
  if (tag === "B" || tag === "STRONG") next.b = true;
  if (tag === "I" || tag === "EM") next.i = true;
  if (tag === "U") next.u = true;
  if (tag === "FONT" && node.getAttribute("size")) {
    const size = FONT_SIZE[node.getAttribute("size")];
    if (size) next.size = size; else delete next.size;
  }
  if (tag === "A" && /^(https?:\/\/|mailto:)/i.test(node.getAttribute("href") || "")) {
    next.href = node.getAttribute("href");
  }
  for (const child of node.childNodes) runsOf(child, next, out);
}

function merged(runs) {
  const out = [];
  for (const run of runs) {
    const last = out[out.length - 1];
    const same = last && ["b", "i", "u", "size", "href"].every((k) => last[k] === run[k]);
    if (same) last.t += run.t; else out.push({ ...run });
  }
  return out.filter((r) => r.t);
}

/** Blocks a line of the editor may hold: Chromium puts a list made on a line INSIDE that line's
 * `<div>`, so a block container is walked, not flattened (the lab's list arrived as one
 * paragraph, its items run together). */
const BLOCKS = new Set(["UL", "OL", "BLOCKQUOTE", "DIV", "P"]);

/** The editor's content as the document the server takes. */
export function readDoc(root) {
  const doc = [];
  let loose = [];
  const flush = () => {
    const runs = merged(loose);
    if (runs.some((r) => r.t.trim())) doc.push({ p: runs });
    loose = [];
  };
  const walk = (nodes) => {
    for (const node of nodes) {
      const tag = node.nodeType === 1 ? node.nodeName : "";
      if (tag === "UL" || tag === "OL") {
        flush();
        const items = [...node.children].filter((li) => li.nodeName === "LI").map((li) => {
          const runs = []; runsOf(li, {}, runs); return merged(runs);
        }).filter((runs) => runs.length);
        if (items.length) doc.push({ [tag.toLowerCase()]: items });
      } else if (tag === "BLOCKQUOTE") {
        flush();
        const runs = []; runsOf(node, {}, runs);
        const text = merged(runs);
        if (text.length) doc.push({ quote: [{ t: text.map((r) => r.t).join("") }] });
      } else if (tag === "DIV" || tag === "P") {
        flush();
        if ([...node.children].some((c) => BLOCKS.has(c.nodeName))) { walk(node.childNodes); flush(); continue; }
        const runs = []; runsOf(node, {}, runs); loose = runs; flush();
      } else {
        runsOf(node, {}, loose);
      }
    }
  };
  walk(root.childNodes);
  flush();
  return doc;
}

function Run({ run }) {
  let out = run.t;
  if (run.u) out = html`<u>${out}</u>`;
  if (run.i) out = html`<em>${out}</em>`;
  if (run.b) out = html`<strong>${out}</strong>`;
  if (run.size) out = html`<span class=${`rt-${run.size}`}>${out}</span>`;
  if (run.href) out = html`<a href=${run.href} rel="noopener noreferrer" target="_blank">${out}</a>`;
  return out;
}

/** A document drawn as elements: what the NOC wrote, as the customer read it. */
export function DocView({ doc }) {
  return html`<div class="rt">${(doc || []).map((block, n) => {
    const [kind, value] = Object.entries(block)[0];
    if (kind === "p") return html`<p key=${n}>${value.map((r, i) => html`<${Run} key=${i} run=${r} />`)}</p>`;
    if (kind === "quote") return html`<blockquote key=${n}>${value.map((r, i) => html`<${Run} key=${i} run=${r} />`)}</blockquote>`;
    const List = kind === "ol" ? "ol" : "ul";
    return html`<${List} key=${n}>${value.map((item, i) => html`<li key=${i}>${item.map((r, j) => html`<${Run} key=${j} run=${r} />`)}</li>`)}<//>`;
  })}</div>`;
}

/** The editable body. Preact never re-renders it: what the person types is theirs. */
class Editor extends Component {
  shouldComponentUpdate() { return false; }

  componentDidMount() {
    const root = this.base;
    for (const block of this.props.initial || []) root.append(this.element(block));
    if (!root.childNodes.length) root.append(globalThis.document.createElement("div"));
  }

  /** One block of a prefilled draft (a quote, an update), built node by node. */
  element(block) {
    const doc = globalThis.document;
    const [kind, value] = Object.entries(block)[0];
    const text = (runs) => runs.map((r) => r.t).join("");
    if (kind === "ul" || kind === "ol") {
      const list = doc.createElement(kind);
      for (const item of value) { const li = doc.createElement("li"); li.textContent = text(item); list.append(li); }
      return list;
    }
    const el = doc.createElement(kind === "quote" ? "blockquote" : "div");
    for (const [i, line] of text(value).split("\n").entries()) {
      if (i) el.append(doc.createElement("br"));
      el.append(doc.createTextNode(line));
    }
    return el;
  }

  render({ label }) {
    return html`<div class="mail-editor" contenteditable=${true} role="textbox" aria-multiline="true"
      aria-label=${label} spellcheck=${true}></div>`;
  }
}

/* ---------- the composer ---------- */

function list(text) {
  return String(text || "").split(/[,;\s]+/).map((a) => a.trim()).filter(Boolean);
}

export class EmailComposer extends Component {
  constructor(props) {
    super(props);
    const d = props.draft || {};
    this.state = {
      to: (d.to || []).join(", "), cc: (d.cc || []).join(", "), bcc: (d.bcc || []).join(", "),
      subject: d.subject || "", copies: !!((d.cc && d.cc.length) || (d.bcc && d.bcc.length)),
      size: "", link: null, busy: false, error: null,
    };
    this.editor = null;
  }

  command(name, value) {
    const editor = this.editor && this.editor.base;
    if (!editor) return;
    editor.focus();
    globalThis.document.execCommand(name, false, value);
  }

  addLink() {
    const url = (this.state.link || "").trim();
    if (!/^(https?:\/\/|mailto:)/i.test(url)) {
      this.setState({ error: "A link starts with https://, http:// or mailto:" });
      return;
    }
    this.command("createLink", url);
    this.setState({ link: null, error: null });
  }

  async send(event) {
    event.preventDefault();
    const { draft = {} } = this.props;
    const doc = readDoc(this.editor.base);
    const body = {
      to: list(this.state.to), cc: list(this.state.cc), bcc: list(this.state.bcc),
      subject: this.state.subject.trim(), doc,
      ...(draft.reply_to ? { reply_to: draft.reply_to } : {}),
    };
    if (!body.to.length) { this.setState({ error: "Who is it to? Add at least one address." }); return; }
    if (!doc.length) { this.setState({ error: "Write the message first." }); return; }
    this.setState({ busy: true, error: null });
    try {
      const sent = await post(`/api/noc/cases/${this.props.sid}/email`, body);
      this.setState({ busy: false });
      this.props.onSent(sent);
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  field(key, label, hint) {
    const id = `mail-${this.props.sid}-${key}`;
    return html`<label class="mail-field" for=${id}><span>${label}</span>
      <input id=${id} type="text" list=${`mail-${this.props.sid}-suggest`} value=${this.state[key]}
        autocomplete="off" spellcheck=${false} placeholder=${hint}
        onInput=${(e) => this.setState({ [key]: e.currentTarget.value })} /></label>`;
  }

  render({ sid, draft = {}, suggest = [], from, onCancel }, st) {
    const tool = (icon, label, run) => html`<button type="button" class="mail-tool" title=${label}
      aria-label=${label} onMouseDown=${(e) => { e.preventDefault(); run(); }}>${icon}</button>`;
    return html`<form class="mail-compose" onSubmit=${(e) => this.send(e)} data-role="email-composer">
      <div class="mail-compose-head">
        <b><${Icon} name="mail" /> ${draft.reply_to ? (draft.reply_all ? "Reply all" : "Reply") : "New email"}</b>
        ${from ? html`<span class="muted">from ${from}</span>` : null}
      </div>
      <datalist id=${`mail-${sid}-suggest`}>${suggest.map((a) => html`<option key=${a} value=${a} />`)}</datalist>
      ${this.field("to", "To", "name@customer.com, …")}
      ${st.copies ? html`${this.field("cc", "Cc", "optional")}${this.field("bcc", "Bcc", "optional — hidden from the others")}`
        : html`<button type="button" class="linkish mail-copies" onClick=${() => this.setState({ copies: true })}>Cc / Bcc</button>`}
      <label class="mail-field"><span>Subject</span>
        <input type="text" value=${st.subject} maxlength="180" placeholder="What it is about"
          onInput=${(e) => this.setState({ subject: e.currentTarget.value })} /></label>
      <div class="mail-toolbar" role="toolbar" aria-label="Formatting">
        ${tool(html`<b>B</b>`, "Bold", () => this.command("bold"))}
        ${tool(html`<i>I</i>`, "Italic", () => this.command("italic"))}
        ${tool(html`<u>U</u>`, "Underline", () => this.command("underline"))}
        <select class="mail-size" aria-label="Size" value=${st.size}
          onChange=${(e) => { this.setState({ size: e.currentTarget.value }); this.command("fontSize", COMMAND_SIZE[e.currentTarget.value]); }}>
          ${SIZES.map(([key, word]) => html`<option key=${key} value=${key}>${word}</option>`)}
        </select>
        ${tool("•", "Bulleted list", () => this.command("insertUnorderedList"))}
        ${tool("1.", "Numbered list", () => this.command("insertOrderedList"))}
        ${tool(html`<${Icon} name="link" />`, "Link", () => this.setState({ link: st.link === null ? "https://" : null }))}
        ${tool("⌫", "Clear formatting", () => this.command("removeFormat"))}
        ${st.link !== null ? html`<span class="mail-link">
          <input type="url" value=${st.link} aria-label="Link address"
            onInput=${(e) => this.setState({ link: e.currentTarget.value })} />
          <button type="button" onMouseDown=${(e) => { e.preventDefault(); this.addLink(); }}>Add link</button></span>` : null}
      </div>
      <${Editor} ref=${(c) => { this.editor = c; }} initial=${draft.doc} label="Message" />
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="mail-compose-foot">
        <span class="hint">Sent from this customer's mailbox. Replies come back onto this feed.</span>
        <button type="button" onClick=${onCancel}>Discard</button>
        <button type="submit" class="primary" disabled=${st.busy} data-role="send-email">
          <${Icon} name="send" /> ${st.busy ? "Sending…" : "Send email"}</button>
      </div>
    </form>`;
  }
}

/* ---------- an email on the feed ---------- */

export function EmailCard({ mail, canReply, onReply }) {
  if (!mail) return null;
  const incoming = mail.direction === "in";
  const people = (label, text) => (text ? html`<span><span class="muted">${label}</span> ${text}</span>` : null);
  return html`<div class=${cx("mail-card", incoming ? "mail-in" : "mail-out")} data-role="email" data-mail=${mail.id}>
    <div class="mail-card-head">
      <span class="mail-dir"><${Icon} name=${incoming ? "inbox" : "send"} /> ${incoming ? "Email received" : "Email sent"}</span>
      ${mail.outcome === "failed" ? html`<span class="badge badge-warn" title=${mail.detail}>not sent</span>` : null}
    </div>
    <div class="mail-card-who">
      ${people("From", mail.from_name ? `${mail.from_name} <${mail.from}>` : mail.from)}
      ${people("To", mail.to)}${people("Cc", mail.cc)}${people("Bcc", mail.bcc)}
    </div>
    <p class="mail-card-subject"><b>${mail.subject || "(no subject)"}</b></p>
    ${mail.doc ? html`<${DocView} doc=${mail.doc} />`
      : html`<div class="rt">${String(mail.text || "").split(/\n{2,}/).map((p, i) => html`<p key=${i}>${p}</p>`)}</div>`}
    ${mail.outcome === "failed" && mail.detail ? html`<p class="hint">${mail.detail}</p>` : null}
    ${canReply ? html`<div class="mail-card-actions">
      <button type="button" data-role="reply" onClick=${() => onReply(mail.id, false)}><${Icon} name="reply" /> Reply</button>
      <button type="button" data-role="reply-all" onClick=${() => onReply(mail.id, true)}><${Icon} name="reply" /> Reply all</button>
    </div>` : null}
  </div>`;
}

/** The server's draft for a reply, a new email, or the customer update. */
export async function draftFor(sid, { replyTo = null, all = false, update = false } = {}) {
  const q = new URLSearchParams();
  if (replyTo) q.set("reply_to", replyTo);
  if (all) q.set("all", "1");
  if (update) q.set("update", "1");
  const text = q.toString();
  return get(`/api/noc/cases/${sid}/email/draft${text ? `?${text}` : ""}`);
}
