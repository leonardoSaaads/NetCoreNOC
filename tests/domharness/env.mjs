/* The sandbox `ui/app.js` is evaluated in: the globals a browser would provide, replaced by
 * deterministic, recording doubles.
 *
 * Four seams are substituted, and each is declared here because a substitution nobody wrote down
 * is a gap in the evidence:
 *
 *   1. **the network** (`fetch`, `EventSource`) — canned responses from the scenario, and every
 *      request recorded. This is not a compromise: recording what the client SENDS is the point
 *      of two of the five captured invariants.
 *   2. **the clock** (`Date.now`, `toLocale*`) — pinned, so two runs produce identical output.
 *   3. **the timers** (`setTimeout`, `setInterval`) — registered and never fired, so nothing
 *      happens between the driver's steps that the driver did not ask for.
 *   4. **d3** — a strict recording double. `app.js` calls d3 only from the graph and timeline
 *      render paths, which are LAYOUT and therefore outside this release's characterisation
 *      boundary (SCOPE-0.12.0 §4). The double implements the exact d3 surface `app.js` uses and
 *      THROWS on anything else, so the day the UI reaches for a new d3 API the harness says so
 *      rather than silently returning undefined.
 *
 * **What substituting d3 costs, stated plainly**: nothing this harness asserts covers the
 * force-directed graph or the timeline SVG. Those are unexecuted by these tests in every sense
 * that matters. They are also exactly what v0.13.0 will rewrite.
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Document, DomEvent, documentFromHTML } from "./dom.mjs";

/** Fixed epoch for every run. Chosen once; changing it changes nothing but the rendered ages. */
export const FIXED_NOW_MS = 1_700_000_000_000;

const CLOCK_PRELUDE = `
  const __fixed = ${FIXED_NOW_MS};
  const __RealDate = Date;
  Date.now = () => __fixed;
  Date.prototype.toLocaleString = function () { return this.toISOString(); };
  Date.prototype.toLocaleTimeString = function () { return this.toISOString().slice(11, 19); };
  Date.prototype.toLocaleDateString = function () { return this.toISOString().slice(0, 10); };
  void __RealDate;
`;

/* ---------- the d3 double ---------- */

const SELECTION_METHODS = [
  "append", "attr", "call", "data", "join", "on", "remove", "select", "selectAll",
  "style", "text", "property", "each", "classed", "enter", "exit", "merge", "filter", "order",
];
const SIM_METHODS = ["force", "on", "nodes", "alpha", "alphaTarget", "restart", "stop", "tick"];
// `sim.force("link")` hands back the FORCE, not the simulation, and `app.js` then calls
// `.links(links)` on it. The double got this wrong first time and said so loudly — a lenient
// double would have returned undefined and left `updateGraph` silently half-executed.
const FORCE_METHODS = ["links", "strength", "id", "distance", "radius", "x", "y", "distanceMax"];

function makeSelection(record, path) {
  const target = function () {};
  return new Proxy(target, {
    apply: () => makeSelection(record, `${path}()`),
    get(_t, key) {
      if (key === "node") return () => makeFakeNode();
      if (key === Symbol.toPrimitive || key === "then" || key === "constructor") return undefined;
      if (typeof key === "symbol") return undefined;
      if (!SELECTION_METHODS.includes(key)) {
        throw new Error(
          `d3 double: app.js called ${path}.${String(key)}(), which the double does not ` +
          `implement. Add it deliberately (tests/domharness/env.mjs) — a double that answered ` +
          `undefined here would let a render path silently do nothing.`,
        );
      }
      return (...args) => {
        record.push(`${path}.${key}`);
        // `join(enterFn)` hands the caller a selection: calling the callback executes more of
        // app.js's own code, which is the point of running it at all.
        if (key === "join" && typeof args[0] === "function") {
          args[0](makeSelection(record, `${path}.${key}<enter>`));
        }
        if (key === "call" && typeof args[0] === "function") {
          args[0](makeSelection(record, `${path}.${key}<self>`));
        }
        return makeSelection(record, `${path}.${key}`);
      };
    },
  });
}

function makeFakeNode() {
  return {
    getBoundingClientRect: () => ({ width: 0, height: 0, x: 0, y: 0, top: 0, left: 0 }),
  };
}

function makeChainableFn(record, path, methods) {
  const fn = (...args) => { record.push(`${path}(arg)`); return args.length === 1 ? 0 : 0; };
  return new Proxy(fn, {
    apply: (t, _this, args) => t(...args),
    get(_t, key) {
      if (typeof key === "symbol") return undefined;
      if (!methods.includes(key)) {
        throw new Error(`d3 double: ${path}.${String(key)} is not implemented (env.mjs)`);
      }
      return (...a) => {
        record.push(`${path}.${key}`);
        // d3's own arity rule, reproduced rather than approximated: `sim.force(name, force)`
        // REGISTERS and returns the simulation for chaining, while `sim.force(name)` READS and
        // returns the force — which is why `sim.force("link").links(links)` works and
        // `sim.force("charge", …).force("link", …)` also works. Collapsing the two broke the
        // second on the first attempt.
        if (key === "force" && a.length === 1) return makeChainableFn(record, `${path}.force`, FORCE_METHODS);
        return makeChainableFn(record, path, methods);
      };
    },
  });
}

const SCALE_METHODS = ["domain", "range", "padding", "ticks", "tickFormat", "nice", "clamp"];

function makeD3(record) {
  const selectionFactories = ["select", "selectAll"];
  const chainFactories = {
    zoom: ["scaleExtent", "on", "transform"],
    drag: ["on"],
    forceManyBody: ["strength"],
    forceLink: ["id", "distance", "strength", "links"],
    forceCollide: ["radius", "strength"],
    forceCenter: ["strength", "x", "y"],
    scaleLinear: SCALE_METHODS,
    scalePoint: SCALE_METHODS,
    axisBottom: ["ticks", "tickFormat", "tickValues", "tickSize"],
    axisLeft: ["ticks", "tickFormat", "tickValues", "tickSize"],
  };
  return new Proxy({}, {
    get(_t, key) {
      if (typeof key === "symbol") return undefined;
      if (selectionFactories.includes(key)) {
        return (sel) => { record.push(`d3.${key}(${sel})`); return makeSelection(record, `d3.${key}`); };
      }
      if (key === "forceSimulation") {
        return () => { record.push("d3.forceSimulation"); return makeChainableFn(record, "sim", SIM_METHODS); };
      }
      if (Object.hasOwn(chainFactories, key)) {
        return (...a) => { record.push(`d3.${key}`); void a; return makeChainableFn(record, `d3.${key}`, chainFactories[key]); };
      }
      throw new Error(
        `d3 double: app.js used d3.${String(key)}, which the double does not implement ` +
        `(tests/domharness/env.mjs). Add it deliberately.`,
      );
    },
  });
}

/* ---------- the network doubles ---------- */

class RecordingNetwork {
  constructor(routes) {
    this.routes = routes;
    this.requests = [];
    this.pending = [];
  }

  fetch(path, init = {}) {
    const method = (init.method || "GET").toUpperCase();
    const body = init.body === undefined ? null : JSON.parse(init.body);
    this.requests.push({ method, path, body, headers: init.headers ?? {} });
    const canned = this.routes[`${method} ${path}`] ?? this.routes[path];
    const promise = Promise.resolve(makeResponse(canned));
    this.pending.push(promise);
    return promise;
  }
}

function makeResponse(canned) {
  if (canned === undefined) {
    // An unrouted request is a 404 rather than a throw: the point of the capability invariant is
    // that the request is NEVER MADE, and a harness that crashed on it would hide the count.
    return {
      ok: false, status: 404,
      headers: { get: () => "application/json" },
      json: async () => ({ detail: "no canned response" }),
      text: async () => "",
    };
  }
  const status = canned.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h) => (h.toLowerCase() === "content-type" ? "application/json" : null) },
    json: async () => canned.json ?? canned,
    text: async () => JSON.stringify(canned.json ?? canned),
  };
}

class FakeEventSource {
  constructor(url, registry) {
    this.url = url;
    this.listeners = new Map();
    this.onerror = null;
    this.closed = false;
    registry.push(this);
  }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  close() { this.closed = true; }
  /** Driver-side: deliver a server-sent event exactly as the browser would. */
  emit(type, data) {
    for (const h of this.listeners.get(type) ?? []) h({ data: JSON.stringify(data) });
  }
}

/* ---------- the sandbox ---------- */

/** Cookie jar. A plain string, exactly as `document.cookie` is, with the browser's split
 * semantics: reading returns `name=value; name=value`, writing sets or replaces ONE pair and
 * carries attributes the reader never sees. The theme preference is persisted here (ADR #172),
 * and modelling it as a dictionary would have hidden the one property that matters — that a
 * write is a single pair and a read is all of them, with no attributes. */
function makeCookieJar() {
  const pairs = new Map();
  return {
    get value() {
      return [...pairs].map(([name, v]) => `${name}=${v}`).join("; ");
    },
    write(raw) {
      const [pair] = String(raw).split(";");
      const at = pair.indexOf("=");
      if (at === -1) return;
      pairs.set(pair.slice(0, at).trim(), pair.slice(at + 1).trim());
    },
    // Driver-side, so a scenario can plant a cookie the way a previous session would have.
    seed(name, value) { pairs.set(name, value); },
    raw: pairs,
  };
}

export function createEnvironment({ indexHtml, routes, dialogs = {}, cookies = {}, hash = "" }) {
  const document = documentFromHTML(indexHtml);
  const d3Calls = [];
  const network = new RecordingNetwork(routes);
  const eventSources = [];
  const dialogCalls = [];
  const timers = [];
  const cookieJar = makeCookieJar();
  for (const [name, value] of Object.entries(cookies)) cookieJar.seed(name, value);

  // `location.hash` is the router's input, so it is a real, writable property here rather than a
  // constant: a scenario navigates by assigning it, exactly as a link click would, and the
  // `hashchange` listener the router registers fires from that assignment.
  const location = {
    hash,
    pathname: "/",
    reload: () => dialogCalls.push({ kind: "reload" }),
  };
  const windowListeners = new Map();
  const window = {
    location,
    addEventListener: (type, handler) => {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(handler);
    },
    removeEventListener: (type, handler) => {
      const list = windowListeners.get(type) ?? [];
      const at = list.indexOf(handler);
      if (at !== -1) list.splice(at, 1);
    },
    matchMedia: (query) => ({ matches: false, media: query, addEventListener() {} }),
  };

  const sandbox = {
    document,
    window,
    d3: makeD3(d3Calls),
    console: { log() {}, warn() {}, error() {} },
    fetch: (path, init) => network.fetch(path, init),
    EventSource: function EventSource(url) { return new FakeEventSource(url, eventSources); },
    location,
    setInterval: (fn, ms) => { timers.push({ kind: "interval", fn, ms }); return timers.length; },
    clearInterval: () => {},
    setTimeout: (fn, ms) => { timers.push({ kind: "timeout", fn, ms }); return timers.length; },
    clearTimeout: () => {},
    queueMicrotask: (fn) => { void Promise.resolve().then(fn); },
    alert: (message) => { dialogCalls.push({ kind: "alert", message }); },
    confirm: (message) => {
      dialogCalls.push({ kind: "confirm", message });
      return dialogs.confirm ?? true;
    },
    prompt: (message, value) => {
      dialogCalls.push({ kind: "prompt", message, value });
      return Object.hasOwn(dialogs, "prompt") ? dialogs.prompt : value;
    },
    Blob: function Blob(parts) { this.parts = parts; },
    URL: { createObjectURL: () => "blob:harness", revokeObjectURL: () => {} },
    // A real browser global the router uses to read a fragment's query string. Node's own
    // implementation, passed through rather than re-modelled: this is a pure parser with no I/O
    // and no clock, so substituting it would add a way for the harness to disagree with a browser
    // for no benefit. (`fetch`, `EventSource`, the clock, the timers and d3 remain doubles, and
    // those five are the whole substitution list.)
    URLSearchParams,
    Intl,
  };
  sandbox.globalThis = sandbox;
  sandbox.window.document = document;
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => cookieJar.value,
    set: (raw) => cookieJar.write(raw),
  });

  const context = vm.createContext(sandbox);
  vm.runInContext(CLOCK_PRELUDE, context, { filename: "harness:clock" });

  const env = {
    context, sandbox, document, network, eventSources, dialogCalls, d3Calls, timers,
    cookies: cookieJar, location, DomEvent, Document,
    /** Fire a window-level event the way the browser does (the router listens for `hashchange`). */
    emitWindow(type, detail = {}) {
      for (const handler of windowListeners.get(type) ?? []) handler({ type, ...detail });
    },
    /** Navigate: set the fragment and deliver `hashchange`, in that order, as a browser does. */
    navigate(to) {
      location.hash = to;
      env.emitWindow("hashchange");
    },
    modules: new Map(),
  };
  return env;
}

/* ---------- the module graph ---------- */

/**
 * Evaluate the UI's ESM entry point and everything it imports, inside `env`'s context.
 *
 * v0.12.0 ran one file through `vm.runInContext`, which cannot see an `import`. v0.13.0's UI is a
 * module graph — an entry point, ~20 view modules, and two vendored assets — so the harness links
 * it with `vm.SourceTextModule` and a resolver that reads **the real files from the real
 * directory**. Two consequences worth stating:
 *
 *   * the vendored Preact and htm the browser would load are the exact bytes evaluated here.
 *     They are not stubbed, not shimmed, and not a second copy; `CHECKSUMS.txt` pins what runs.
 *   * every module the entry point imports is evaluated, because the UI's imports are static.
 *     **Module evaluation is not view activation**: the registry holds every view's component
 *     whatever the principal's role, and what a role's capabilities decide is whether the
 *     component is ever *mounted*. Invariant 5 is about requests, and a module that was loaded
 *     but never mounted issues none.
 *
 * `--experimental-vm-modules` is required and `domdriver.py` passes it. Still stdlib-only: no
 * npm, no loader hook, no transform. The resolver refuses a specifier that escapes the UI
 * directory, so a scenario cannot make the harness read outside what ships.
 */
export async function evaluateModules(env, { uiDir, entry }) {
  const root = path.resolve(uiDir);

  /* Instantiate (never link) one module and cache it by absolute path.
   *
   * `link()` walks the whole graph itself, so the resolver must only HAND BACK a module — calling
   * `link()` on it from inside the resolver is what produced "request … is from a module not been
   * linked" on the first attempt: a diamond (every view imports `dom.js`) reached the shared
   * module while its own linking was still in flight. One `link()` at the entry, and the caching
   * here is what makes the diamond collapse to a single instance — which matters, because the
   * proof reads `esc` off the same `dom.js` instance the running UI imported.
   */
  const instantiate = (file) => {
    const cached = env.modules.get(file);
    if (cached) return cached;
    if (!file.startsWith(root + path.sep) && file !== path.join(root, entry)) {
      throw new Error(`module ${file} is outside the UI directory; the harness loads what ships`);
    }
    const module = new vm.SourceTextModule(fs.readFileSync(file, "utf8"), {
      context: env.context,
      identifier: path.relative(root, file),
    });
    env.modules.set(file, module);
    return module;
  };

  const entryModule = instantiate(path.join(root, entry));
  await entryModule.link((specifier, referrer) => {
    if (!specifier.startsWith(".")) {
      throw new Error(
        `${referrer.identifier} imports the bare specifier ${JSON.stringify(specifier)}. The ` +
        `shipped UI resolves every import by relative path, because a bare specifier needs an ` +
        `import map and an import map is an inline <script> the CSP forbids.`,
      );
    }
    return instantiate(
      path.resolve(path.dirname(path.join(root, referrer.identifier)), specifier),
    );
  });
  await entryModule.evaluate();
  env.entry = entryModule;
  return entryModule;
}

/** Drain the microtask queue until the doubles have no outstanding promises. Deterministic. */
export async function settle(env, rounds = 12) {
  for (let i = 0; i < rounds; i += 1) {
    const outstanding = env.network.pending.splice(0);
    if (outstanding.length) await Promise.allSettled(outstanding);
    await new Promise((resolve) => setImmediate(resolve));
  }
}
