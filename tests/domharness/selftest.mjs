/* Conformance tests for the harness's own DOM — the instrument's vacuity check.
 *
 * Appendix B of this project's build prompts records "measuring nothing and concluding CLOSED" as
 * a trap that has caught this repository before. A hand-written DOM is a fresh way to fall into
 * it: if `append` cloned instead of moved, or `removeChild` destroyed instead of detached, the
 * v0.7.5 invariant would pass for a property of the instrument rather than a property of
 * `ui/app.js`, and the green would mean nothing.
 *
 * Every assertion below is a semantic the captured invariants actually depend on. This file is not
 * a general DOM conformance suite and does not pretend to be one — it covers what is load-bearing.
 */

import fs from "node:fs";
import path from "node:path";
import { DomEvent, Document, documentFromHTML } from "./dom.mjs";

const results = [];

function check(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, detail: String(error && error.message ? error.message : error) });
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

export function runSelfTests() {
  results.length = 0;

  check("append moves an already-parented node rather than copying it", () => {
    const d = new Document();
    const a = d.createElement("div");
    const b = d.createElement("div");
    const child = d.createElement("span");
    a.append(child);
    b.append(child);
    assert(child.parentNode === b, "the node did not move to its new parent");
    assert(a.childNodes.length === 0, "the node was copied: the old parent still holds it");
    assert(b.childNodes.length === 1, "the new parent does not hold exactly one child");
  });

  check("removeChild detaches without destroying children or listeners", () => {
    const d = new Document();
    const parent = d.createElement("div");
    const node = d.createElement("div");
    const grandchild = d.createElement("b");
    let fired = 0;
    node.addEventListener("click", () => { fired += 1; });
    node.append(grandchild);
    parent.append(node);
    parent.removeChild(node);
    assert(node.parentNode === null, "the detached node still claims a parent");
    assert(node.firstChild === grandchild, "detaching destroyed the node's own children");
    node.dispatchEvent(new DomEvent("click"));
    assert(fired === 1, "detaching destroyed the node's listeners");
  });

  check("a cleared-then-reappended node keeps its identity and its listeners", () => {
    // This is `renderSituations`'s held-card path, reduced to its DOM primitives. If this were
    // wrong, the v0.7.5 invariant would be measuring the harness.
    const d = new Document();
    const list = d.createElement("div");
    const held = d.createElement("div");
    let clicks = 0;
    held.addEventListener("click", () => { clicks += 1; });
    list.append(held);
    while (list.firstChild) list.removeChild(list.firstChild);   // app.js's clear()
    assert(list.childNodes.length === 0, "clear() left children behind");
    list.append(held);
    assert(list.firstChild === held, "the re-appended node is not the same node");
    held.dispatchEvent(new DomEvent("click"));
    assert(clicks === 1, "the re-appended node lost its listener");
  });

  check("isConnected distinguishes a document-rooted node from a detached one", () => {
    const d = documentFromHTML("<html><body><div id='x'></div></body></html>");
    const x = d.getElementById("x");
    assert(x.isConnected === true, "a document-rooted node reported itself disconnected");
    x.remove();
    assert(x.isConnected === false, "a detached node reported itself connected");
  });

  check("classList.toggle honours its force argument in both directions", () => {
    const d = new Document();
    const el = d.createElement("div");
    el.classList.toggle("active", true);
    assert(el.classList.contains("active"), "toggle(name, true) did not add");
    el.classList.toggle("active", true);
    assert(el.className.split(/\s+/).filter((c) => c === "active").length === 1, "toggle duplicated the class");
    el.classList.toggle("active", false);
    assert(!el.classList.contains("active"), "toggle(name, false) did not remove");
    el.classList.toggle("active");
    assert(el.classList.contains("active"), "bare toggle did not flip it back on");
  });

  check("dataset reads and writes data-* attributes", () => {
    const d = new Document();
    const el = d.createElement("div");
    el.setAttribute("data-panel", "audit");
    assert(el.dataset.panel === "audit", "dataset did not read data-panel");
    el.dataset.sid = "7";
    assert(el.getAttribute("data-sid") === "7", "dataset did not write data-sid");
  });

  check("textContent replaces every child with one text node", () => {
    const d = new Document();
    const el = d.createElement("div");
    el.append(d.createElement("b"), d.createTextNode("x"));
    el.textContent = "only";
    assert(el.childNodes.length === 1, "textContent left more than one child");
    assert(el.textContent === "only", "textContent did not read back what was written");
  });

  check("the attribute selector app.js prunes with resolves", () => {
    const d = documentFromHTML(
      "<html><body><section class='panel' data-panel='users'></section>" +
      "<section class='panel' data-panel='audit'></section></body></html>",
    );
    const found = d.querySelector('.panel[data-panel="audit"]');
    assert(found !== null, "the pruning selector matched nothing");
    assert(found.dataset.panel === "audit", "the pruning selector matched the wrong panel");
    assert(d.querySelectorAll(".panel").length === 2, "the class selector did not find both panels");
  });

  check("the descendant combinator app.js reads tabs with resolves", () => {
    const d = documentFromHTML(
      "<html><body><nav class='tabs'><button>A</button><button>B</button></nav>" +
      "<nav class='other'><button>C</button></nav></body></html>",
    );
    const buttons = d.querySelectorAll("nav.tabs button");
    assert(buttons.length === 2, `nav.tabs button matched ${buttons.length}, expected 2`);
  });

  check("an unrecognised selector throws rather than matching nothing", () => {
    const d = new Document();
    let threw = false;
    try { d.querySelectorAll("div > span"); } catch { threw = true; }
    assert(threw, "a selector outside the grammar matched nothing silently");
  });

  check("events bubble to an ancestor listener", () => {
    const d = new Document();
    const outer = d.createElement("div");
    const inner = d.createElement("button");
    outer.append(inner);
    let seen = null;
    outer.addEventListener("click", (e) => { seen = e.target; });
    inner.dispatchEvent(new DomEvent("click"));
    assert(seen === inner, "the ancestor listener did not see the descendant's event");
  });

  check("select.value defaults to the first option, and an explicit set wins", () => {
    const d = new Document();
    const select = d.createElement("select");
    for (const value of ["open", "closed"]) {
      const option = d.createElement("option");
      option.setAttribute("value", value);
      select.append(option);
    }
    assert(select.value === "open", `select defaulted to ${JSON.stringify(select.value)}, not the first option`);
    select.value = "closed";
    assert(select.value === "closed", "an explicit value assignment did not win");
  });

  check("input.value reads the value attribute until a property assignment overrides it", () => {
    const d = new Document();
    const input = d.createElement("input");
    input.setAttribute("value", "10.0.0.0/8");
    assert(input.value === "10.0.0.0/8", "input.value did not read the attribute");
    input.value = "";
    assert(input.value === "", "the property assignment did not override the attribute");
  });

  check("innerHTML really parses markup into elements", () => {
    // The one that makes the escaping invariant non-vacuous: if this were a no-op, a render path
    // that bypassed esc() could not construct an injected element and the invariant would pass
    // for a property of the harness. See dom.mjs and html.mjs.
    const d = new Document();
    const el = d.createElement("div");
    el.innerHTML = '<img src="x" onerror="alert(1)"><b>hi</b>';
    const tags = el.children.map((c) => c.tagName);
    assert(tags.includes("img"), `innerHTML did not build an <img>; built ${JSON.stringify(tags)}`);
    assert(tags.includes("b"), "innerHTML did not build a <b>");
    assert(el.children[0].getAttribute("onerror") === "alert(1)", "innerHTML dropped the attribute");
  });

  check("insertBefore places a node at the reference position", () => {
    const d = new Document();
    const parent = d.createElement("div");
    const a = d.createElement("i");
    const c = d.createElement("u");
    const b = d.createElement("s");
    parent.append(a, c);
    parent.insertBefore(b, c);
    assert(parent.children.map((n) => n.tagName).join("") === "isu", "insertBefore ordered wrongly");
  });

  check("a document fragment appends its children and empties itself", () => {
    const d = new Document();
    const parent = d.createElement("div");
    const frag = d.createDocumentFragment();
    frag.append(d.createElement("b"), d.createElement("i"));
    parent.append(frag);
    assert(parent.children.length === 2, "the fragment's children did not transfer");
    assert(frag.childNodes.length === 0, "the fragment kept its children after being appended");
  });

  check("the shipped index.html parses into the mount point the console needs", () => {
    // v0.13.0: the document is a mount point. It carries #root and the two script tags and
    // nothing else — the screens are rendered, not declared — so this checks the two facts the
    // harness actually depends on rather than eight ids that no longer exist.
    const source = fs.readFileSync(path.join(import.meta.dirname, "..", "..",
      "src", "netcorenoc", "ui", "index.html"), "utf8");
    const d = documentFromHTML(source);
    assert(d.getElementById("root") !== null, "index.html parsed without #root");
    const scripts = d.querySelectorAll("script");
    assert(scripts.length === 2, `expected two script tags, parsed ${scripts.length}`);
    assert(scripts[1].getAttribute("type") === "module", "app.js is not loaded as a module");
    // The old ids must be GONE, not merely unused. `#sidebar` was the work area and is the
    // collision draft §1.1 required this release to resolve; a document that still carried it
    // would let a guard match the old id and report that it had found navigation.
    for (const id of ["sidebar", "tabs", "sits", "app", "login"]) {
      assert(d.getElementById(id) === null, `index.html still declares #${id}`);
    }
  });

  /* ---------- v0.13.0: the five additions, each covered behaviourally ---------- */

  check("createElementNS builds an element carrying its namespace and localName", () => {
    const d = new Document();
    const svg = d.createElementNS("http://www.w3.org/2000/svg", "svg");
    assert(svg.namespaceURI === "http://www.w3.org/2000/svg", "namespaceURI was not recorded");
    assert(svg.localName === "svg", "localName was not set");
    const div = d.createElementNS("http://www.w3.org/1999/xhtml", "div");
    assert(div.namespaceURI === "http://www.w3.org/1999/xhtml", "the XHTML namespace was lost");
    assert(d.namespaceURI === "http://www.w3.org/1999/xhtml",
      "the document has no namespaceURI; render() reads it to decide what it builds");
  });

  check("an on<type> property EXISTS and registers a real listener", () => {
    // THE IMPORTANT ONE. Preact maps `onClick` to a listener type by asking `"onclick" in node`.
    // Without the property it registers the type "Click", no dispatched `click` ever reaches it,
    // and the UI renders perfectly while responding to nothing — with every assertion about
    // rendered markup still green. So this asserts BOTH halves: the property is `in` the node,
    // and setting it produces a handler a dispatched lower-case event actually reaches.
    const d = new Document();
    const button = d.createElement("button");
    assert("onclick" in button, '"onclick" is not `in` an element; Preact would register "Click"');
    let fired = 0;
    button.onclick = () => { fired += 1; };
    button.dispatchEvent(new DomEvent("click"));
    assert(fired === 1, `the on<type> handler did not fire (fired ${fired} times)`);
    // Reassigning replaces rather than stacking, as a browser does.
    button.onclick = () => { fired += 10; };
    button.dispatchEvent(new DomEvent("click"));
    assert(fired === 11, `reassigning on<type> stacked handlers instead of replacing (${fired})`);
    button.onclick = null;
    button.dispatchEvent(new DomEvent("click"));
    assert(fired === 11, "clearing on<type> did not remove the listener");
  });

  check("addEventListener refuses a capitalised event type", () => {
    // The tripwire that makes the failure above impossible to reintroduce silently.
    const d = new Document();
    const el = d.createElement("div");
    let threw = false;
    try { el.addEventListener("Click", () => {}); } catch { threw = true; }
    assert(threw, "a capitalised event type was accepted; a dead listener would go unnoticed");
    // The control: the lower-case form must still work, or this would refuse everything.
    let fired = false;
    el.addEventListener("click", () => { fired = true; });
    el.dispatchEvent(new DomEvent("click"));
    assert(fired, "the lower-case listener did not fire; the refusal above is over-broad");
  });

  check("style exposes setProperty and cssText", () => {
    const d = new Document();
    const el = d.createElement("div");
    el.style.setProperty("width", "12px");
    assert(el.style.width === "12px", "setProperty did not set the property");
    el.style.cssText = "";
    assert(el.style.cssText === "", "cssText is not assignable");
    el.style.removeProperty("width");
    assert(el.style.width === undefined, "removeProperty did not remove it");
  });

  check("id is a writable property, as a browser reflects it", () => {
    const d = new Document();
    const el = d.createElement("div");
    el.id = "work";
    assert(el.getAttribute("id") === "work", "assigning .id did not set the attribute");
    assert(el.id === "work", "reading .id back did not return it");
  });

  return { results, passed: results.filter((r) => r.ok).length, failed: results.filter((r) => !r.ok).length };
}
