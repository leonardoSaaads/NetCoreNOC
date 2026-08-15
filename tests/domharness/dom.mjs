/* The DOM the harness executes `ui/app.js` against.
 *
 * ## What this is, stated precisely, because the release's central claim rests on the wording
 *
 * This is a **purpose-built DOM implementation**, roughly 300 lines of stdlib-only JavaScript.
 * It is not a browser and it is not jsdom. It implements the subset of the DOM that
 * `ui/app.js` actually touches, and it **throws** on anything outside that subset rather than
 * returning `undefined`, so the day `app.js` reaches for something new the harness says so
 * instead of quietly measuring nothing.
 *
 * Why a hand-written DOM rather than jsdom: jsdom is an npm package, and an npm package means
 * `package.json`, `node_modules/` and a network install in a project whose sixth constitutional
 * principle is "a static UI with no build step and no npm". A harness that needs `npm install`
 * to run is a harness that skips on the maintainer's machine — the single failure mode this
 * release is most likely to die of. This one needs Node and nothing else. ADR #167 records the
 * decision and the objection to it.
 *
 * ## Why you may believe it is faithful
 *
 * You should not believe it on the strength of this comment. Two things evidence it:
 *
 *   * `selftest.mjs` asserts this file's own semantics — that `append` MOVES an already-parented
 *     node, that `removeChild` DETACHES without destroying listeners, that `classList.toggle`
 *     honours its force argument — the handful of behaviours the captured invariants actually
 *     depend on. A DOM that got those wrong could make an invariant test pass vacuously.
 *   * every invariant built on this DOM is demonstrated **red** under an injected defect in
 *     `docs/gates/v0.12.0-guard-demonstrations.md`. A DOM too lenient to notice the defect
 *     would show green there, and the demonstration would have failed instead of the code.
 *
 * ## What it deliberately does not do
 *
 * No layout, no CSS cascade, no `getComputedStyle`, no focus model, no selection, no history,
 * no real event loop ordering beyond microtasks. `getBoundingClientRect()` returns zeroes.
 * Nothing here can characterise anything visual, which is consistent with the release's
 * characterisation boundary: invariants only, never layout.
 */

import { parseHTML } from "./html.mjs";

const ELEMENT_NODE = 1;
const TEXT_NODE = 3;
const FRAGMENT_NODE = 11;

/** Selector grammar this DOM understands. Anything else throws — see `matchesSimple`. */
const SIMPLE = /^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/;

class DomNode {
  constructor(ownerDocument) {
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.childNodes = [];
    this._listeners = new Map();
  }

  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null; }
  get children() { return this.childNodes.filter((n) => n.nodeType === ELEMENT_NODE); }

  get nextSibling() {
    if (!this.parentNode) return null;
    const kids = this.parentNode.childNodes;
    return kids[kids.indexOf(this) + 1] ?? null;
  }

  /** True when this node is reachable from the document root. The held-card invariant reads it. */
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === this.ownerDocument || n === this.ownerDocument?.documentElement;
  }

  appendChild(child) {
    // A node that already has a parent MOVES. `renderSituations` re-appends a harvested detail
    // node, and if this cloned instead of moved the v0.7.5 invariant would pass for the wrong
    // reason. `selftest.mjs::append moves` is the guard on this line.
    if (child.parentNode) child.parentNode.removeChild(child);
    if (child.nodeType === FRAGMENT_NODE) {
      for (const grandchild of [...child.childNodes]) this.appendChild(grandchild);
      return child;
    }
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  append(...nodes) {
    for (const n of nodes) {
      this.appendChild(typeof n === "string" ? this.ownerDocument.createTextNode(n) : n);
    }
  }

  prepend(...nodes) {
    const first = this.firstChild;
    for (const n of nodes) {
      const node = typeof n === "string" ? this.ownerDocument.createTextNode(n) : n;
      first ? this.insertBefore(node, first) : this.appendChild(node);
    }
  }

  insertBefore(node, reference) {
    if (reference == null) return this.appendChild(node);
    const at = this.childNodes.indexOf(reference);
    if (at === -1) throw new Error("insertBefore: reference node is not a child");
    if (node.parentNode) node.parentNode.removeChild(node);
    node.parentNode = this;
    this.childNodes.splice(at, 0, node);
    return node;
  }

  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at === -1) throw new Error("removeChild: not a child");
    // DETACH, do not destroy: the node keeps its own children, attributes and listeners, which
    // is precisely what lets `clear(sits)` be survivable by a held card.
    this.childNodes.splice(at, 1);
    child.parentNode = null;
    return child;
  }

  remove() { if (this.parentNode) this.parentNode.removeChild(this); }

  replaceChildren(...nodes) {
    for (const c of [...this.childNodes]) this.removeChild(c);
    this.append(...nodes);
  }

  get textContent() {
    if (this.nodeType === TEXT_NODE) return this._data;
    return this.childNodes.map((c) => c.textContent).join("");
  }

  set textContent(value) {
    if (this.nodeType === TEXT_NODE) { this._data = String(value); return; }
    for (const c of [...this.childNodes]) this.removeChild(c);
    if (value !== "") this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }

  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const list = this._listeners.get(type);
    const at = list ? list.indexOf(handler) : -1;
    if (at !== -1) list.splice(at, 1);
  }

  /** Dispatch with bubbling, so a listener on an ancestor sees a child's event as a browser would. */
  dispatchEvent(event) {
    event.target ??= this;
    const path = [];
    for (let n = this; n; n = n.parentNode) path.push(n);
    for (const node of path) {
      const handlers = node._listeners.get(event.type);
      if (!handlers) continue;
      event.currentTarget = node;
      for (const h of [...handlers]) h.call(node, event);
      if (event.cancelBubble) break;
    }
    return !event.defaultPrevented;
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }

  querySelectorAll(selector) {
    const steps = selector.trim().split(/\s+/);
    // Validate EVERY step before walking. Validating lazily inside the loop meant an unmatched
    // first step emptied the candidate set and the remaining steps were never checked, so
    // `div > span` returned [] instead of throwing — the harness's own self-test caught it
    // (`selftest.mjs::an unrecognised selector throws rather than matching nothing`). A guard
    // that stops guarding once the input is empty is this repository's F51 in miniature.
    for (const step of steps) assertSupportedSelector(step);
    let matched = [this];
    for (const step of steps) {
      const next = [];
      for (const scope of matched) {
        for (const el of descendants(scope)) {
          if (matchesSimple(el, step) && !next.includes(el)) next.push(el);
        }
      }
      matched = next;
    }
    return matched;
  }
}

function* descendants(node) {
  for (const child of node.childNodes) {
    if (child.nodeType === ELEMENT_NODE) {
      yield child;
      yield* descendants(child);
    }
  }
}

/** The bounded selector grammar. THROWS on anything richer so drift is loud, never silent. */
function assertSupportedSelector(selector) {
  if (selector.startsWith("#") || SIMPLE.test(selector)) return;
  throw new Error(
    `the harness DOM understands tag/.class/[attr="value"] selectors and descendant ` +
    `combinators only; app.js used ${JSON.stringify(selector)}. Extend the grammar ` +
    `deliberately rather than letting an unrecognised selector match nothing.`,
  );
}

function matchesSimple(el, selector) {
  if (selector.startsWith("#")) return el.getAttribute("id") === selector.slice(1);
  const parts = SIMPLE.exec(selector);
  if (!parts) return false;
  const [, tag, classes, attrs] = parts;
  if (tag && el.tagName !== tag.toLowerCase()) return false;
  for (const cls of classes.split(".").filter(Boolean)) {
    if (!el.classList.contains(cls)) return false;
  }
  for (const [, name, value] of attrs.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    if (value === undefined) { if (el.getAttribute(name) == null) return false; }
    else if (el.getAttribute(name) !== value) return false;
  }
  return true;
}

class Text extends DomNode {
  constructor(ownerDocument, data) {
    super(ownerDocument);
    this.nodeType = TEXT_NODE;
    this._data = String(data);
  }
  get data() { return this._data; }
  set data(v) { this._data = String(v); }
}

class DocumentFragment extends DomNode {
  constructor(ownerDocument) {
    super(ownerDocument);
    this.nodeType = FRAGMENT_NODE;
  }
}

class Element extends DomNode {
  constructor(ownerDocument, tagName) {
    super(ownerDocument);
    this.nodeType = ELEMENT_NODE;
    this.tagName = tagName.toLowerCase();
    this._attrs = new Map();
    this.style = makeStyle();
    this.classList = makeClassList(this);
    this.dataset = makeDataset(this);
    // Form-control state. `checked` is read by the partial-split gesture.
    this.checked = false;
    this._value = undefined;
  }

  /* Form-control `value`, which is NOT simply the attribute.
   *
   * This is here because getting it wrong changed an observable request. `app.js` reads
   * `$("fltStatus").value` when composing `/api/situations`, and a `<select>` whose property was
   * a bare `""` produced `GET /api/situations?limit=50` where a browser produces
   * `…&status=open` — the first option is selected by default. A harness that models the client
   * wrongly reports the client's contract with the server wrongly, which is exactly the class of
   * error two of the five invariants exist to detect. `selftest.mjs` guards all three branches.
   */
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === "select") {
      const first = this.children.find((c) => c.tagName === "option");
      return first ? first.value : "";
    }
    if (this.tagName === "textarea") return this.textContent;
    const attr = this.getAttribute("value");
    if (attr != null) return attr;
    return this.tagName === "option" ? this.textContent : "";
  }

  set value(v) { this._value = String(v); }

  get className() { return this._attrs.get("class") ?? ""; }
  set className(v) { this._attrs.set("class", String(v)); }

  get title() { return this._attrs.get("title") ?? ""; }
  set title(v) { this._attrs.set("title", String(v)); }

  get id() { return this._attrs.get("id") ?? ""; }

  getAttribute(name) { return this._attrs.has(name) ? this._attrs.get(name) : null; }
  setAttribute(name, value) { this._attrs.set(name.toLowerCase(), String(value)); }
  removeAttribute(name) { this._attrs.delete(name.toLowerCase()); }
  hasAttribute(name) { return this._attrs.has(name.toLowerCase()); }
  get attributes() { return [...this._attrs].map(([name, value]) => ({ name, value })); }

  /* `innerHTML` is implemented, and implemented as a REAL parse, on purpose: see html.mjs. The
   * shipped `app.js` never touches it — that is the property under test, and an instrument that
   * could not express the violation could not test for it. */
  get innerHTML() { return this.childNodes.map(serialize).join(""); }
  set innerHTML(markup) {
    for (const c of [...this.childNodes]) this.removeChild(c);
    build(this.ownerDocument, parseHTML(String(markup)), this);
  }

  getBoundingClientRect() { return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }; }
  focus() {}
  click() { this.dispatchEvent(new DomEvent("click")); }
}

function makeStyle() {
  // A plain property bag. `style.display = "none"` and `style.width = "12px"` are the only two
  // the UI sets, both through CSSOM because the CSP forbids inline style attributes.
  return {};
}

function makeClassList(el) {
  const read = () => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
  const write = (list) => el.setAttribute("class", list.join(" "));
  return {
    add: (...names) => { const l = read(); for (const n of names) if (!l.includes(n)) l.push(n); write(l); },
    remove: (...names) => write(read().filter((c) => !names.includes(c))),
    contains: (name) => read().includes(name),
    toggle: (name, force) => {
      const has = read().includes(name);
      const want = force === undefined ? !has : !!force;
      if (want && !has) el.setAttribute("class", [...read(), name].join(" "));
      if (!want && has) write(read().filter((c) => c !== name));
      return want;
    },
    get length() { return read().length; },
  };
}

function makeDataset(el) {
  const toAttr = (key) => `data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
  return new Proxy({}, {
    get: (_t, key) => (typeof key === "string" ? el.getAttribute(toAttr(key)) ?? undefined : undefined),
    set: (_t, key, value) => { el.setAttribute(toAttr(key), String(value)); return true; },
    has: (_t, key) => el.hasAttribute(toAttr(key)),
  });
}

export class DomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.target = init.target ?? null;
    this.currentTarget = null;
    this.defaultPrevented = false;
    this.cancelBubble = false;
    Object.assign(this, init);
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.cancelBubble = true; }
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

/** Serialisation is for `innerHTML` only. No test asserts over it — see the escaping invariant. */
function serialize(node) {
  if (node.nodeType === TEXT_NODE) return node.data.replace(/[&<>]/g, (c) => ESCAPES[c]);
  const attrs = node.attributes
    .map(({ name, value }) => ` ${name}="${value.replace(/[&<>"]/g, (c) => ESCAPES[c])}"`)
    .join("");
  const inner = node.childNodes.map(serialize).join("");
  return `<${node.tagName}${attrs}>${inner}</${node.tagName}>`;
}

function build(document, parsed, parent) {
  for (const node of parsed) {
    if (Object.hasOwn(node, "text")) {
      if (node.text.trim() !== "" || node.text.includes(" ")) {
        parent.appendChild(document.createTextNode(node.text));
      }
      continue;
    }
    const el = document.createElement(node.tag);
    for (const [name, value] of Object.entries(node.attrs)) el.setAttribute(name, value);
    parent.appendChild(el);
    build(document, node.children, el);
  }
}

export class Document extends DomNode {
  constructor() {
    super(null);
    this.ownerDocument = this;
    this.nodeType = 9;
    this.documentElement = null;
    this.body = null;
  }

  createElement(tag) { return new Element(this, tag); }
  createTextNode(data) { return new Text(this, data); }
  createDocumentFragment() { return new DocumentFragment(this); }

  getElementById(id) {
    for (const el of descendants(this)) if (el.getAttribute("id") === id) return el;
    return null;
  }
}

/** Build a document from an HTML source string. */
export function documentFromHTML(source) {
  const document = new Document();
  build(document, parseHTML(source), document);
  document.documentElement = document.children.find((e) => e.tagName === "html") ?? null;
  document.body =
    (document.documentElement ?? document).querySelector("body") ?? document.documentElement;
  return document;
}
