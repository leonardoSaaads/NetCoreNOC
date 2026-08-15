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

export function createEnvironment({ indexHtml, appJs, routes, dialogs = {} }) {
  const document = documentFromHTML(indexHtml);
  const d3Calls = [];
  const network = new RecordingNetwork(routes);
  const eventSources = [];
  const dialogCalls = [];
  const timers = [];

  const window = {
    addEventListener: (type, handler) => document.addEventListener(type, handler),
    location: { reload: () => dialogCalls.push({ kind: "reload" }) },
  };

  const sandbox = {
    document,
    window,
    d3: makeD3(d3Calls),
    console: { log() {}, warn() {}, error() {} },
    fetch: (path, init) => network.fetch(path, init),
    EventSource: function EventSource(url) { return new FakeEventSource(url, eventSources); },
    location: window.location,
    setInterval: (fn, ms) => { timers.push({ kind: "interval", fn, ms }); return timers.length; },
    clearInterval: () => {},
    setTimeout: (fn, ms) => { timers.push({ kind: "timeout", fn, ms }); return timers.length; },
    clearTimeout: () => {},
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
  };
  sandbox.globalThis = sandbox;
  sandbox.window.document = document;

  const context = vm.createContext(sandbox);
  vm.runInContext(CLOCK_PRELUDE, context, { filename: "harness:clock" });
  // THE LINE THIS RELEASE EXISTS FOR: `ui/app.js`, evaluated.
  vm.runInContext(appJs, context, { filename: "ui/app.js" });

  return { context, sandbox, document, network, eventSources, dialogCalls, d3Calls, timers, DomEvent, Document };
}

/** Drain the microtask queue until the doubles have no outstanding promises. Deterministic. */
export async function settle(env, rounds = 12) {
  for (let i = 0; i < rounds; i += 1) {
    const outstanding = env.network.pending.splice(0);
    if (outstanding.length) await Promise.allSettled(outstanding);
    await new Promise((resolve) => setImmediate(resolve));
  }
}
