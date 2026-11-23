/* The brokers, as the console lists them (ADR #494) — one read for the whole Broker plane.
 *
 * A broker is one monitored network, owned by one customer: "Claro Nordeste", "Claro Sul". The
 * sidebar's *Brokers* group names each one and, under the one open, its five screens — Network
 * graph, Timeline, Entities, Maintenance and Learning — each showing that network alone. The list
 * is read here while the Broker plane is on screen (`brokers.read`, every role; the server sends
 * a scoped person only the brokers they can see), twenty seconds apart, and at once after a screen
 * changes one (`refreshBrokers()`).
 *
 * **Which broker the live stream follows** is the route's: inside a broker it is that broker; on the
 * Overview or Situations it is `?broker=` when one is chosen; anywhere else every broker
 * (`brokerOfRoute`). The shell hands the answer to `store.setBroker`, which re-opens the stream.
 */

import { get } from "./api.js";
import { can } from "./session.js";
import { setBroker } from "./store.js";

const POLL_MS = 20000;

let brokers = null;
let timer = null;
let last = null;
const listeners = new Set();

/** A broker's five screens, in the order the sidebar shows them, with what each one reads. */
export const PARTS = [
  ["graph", "Network graph", "graph", ["graph.read"]],
  ["timeline", "Timeline", "timeline", ["timeline.read"]],
  ["entities", "Entities", "entities", ["entities.read"]],
  ["maintenance", "Maintenance", "maintenance", ["mw.read"]],
  ["learning", "Learning", "labelling", ["situations.read", "model.read"]],
];

/** The screens of a broker this session may open. */
export function partsFor() {
  return PARTS.filter(([, , , caps]) => caps.every((c) => can(c)));
}

/** How each intake reads on screen: a name and one line. */
export const INTAKE = {
  port: ["Own port + ranges", "Its own UDP port on this server; its ranges on the standard port."],
  proxy: ["Remote proxy", "A NetCoreNOC proxy in its network forwards its traps over HTTPS."],
  cidr: ["Ranges only", "Its ranges (CIDR) on the standard port. Addresses may not repeat."],
};

/** A broker's mark: the globe for one reached through a remote proxy, else the network. */
export function brokerIcon(broker) { return broker && broker.intake === "proxy" ? "globe" : "network"; }

/** The brokers last read, or null before the first read. */
export function brokerList() { return brokers; }

export function brokerById(id) {
  return (brokers || []).find((b) => String(b.id) === String(id)) || null;
}

export function brokerName(id) {
  const found = brokerById(id);
  return found ? found.name : id == null ? "All brokers" : `Broker ${id}`;
}

/** The broker opened last, so an old address (`#/graph`) lands on the network being worked. */
export function lastBroker() { return last; }

export function rememberBroker(id) { if (id != null) last = Number(id); }

/** Call `listener(brokers)` after each read; returns the unsubscribe. */
export function onBrokers(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function refreshBrokers() {
  if (!can("brokers.read")) return;
  try {
    brokers = (await get("/api/brokers")).brokers;
    for (const listener of listeners) listener(brokers);
  } catch { /* the list stays as it was; the next read tries again */ }
}

/** Read while `on` (the Broker plane is shown), and stop reading when it is not. */
export function watchBrokers(on) {
  const wanted = Boolean(on) && can("brokers.read");
  if (wanted && timer === null) {
    timer = setInterval(refreshBrokers, POLL_MS);
    refreshBrokers();
  } else if (!wanted && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

/** `#/brokers/{id}/{part}`, with a query when one is given. */
export function partHref(id, part, query) {
  const q = query ? `?${query}` : "";
  return part ? `#/brokers/${id}/${part}${q}` : `#/brokers/${id}${q}`;
}

/** The broker the live stream should follow on this address, or null for every broker. */
export function brokerOfRoute(fragment) {
  const raw = String(fragment || "").replace(/^#\/?/, "");
  const [path, query] = raw.split("?", 2);
  const [view, id] = path.split("/");
  if (view === "brokers" && Number(id)) return Number(id);
  if (view === "overview" || view === "situations" || view === "") {
    const chosen = new URLSearchParams(query || "").get("broker");
    return Number(chosen) || null;
  }
  return null;
}

/** Called by the shell on every route: list the brokers while the Broker plane is `shown`, and
 *  make the live figures follow the broker the address is in, or every broker (ADR #496). */
export function followRoute(fragment, shown) {
  watchBrokers(shown);
  const broker = brokerOfRoute(fragment);
  rememberBroker(broker);
  setBroker(broker);
}

/** The sidebar's *Situations* count: those not resolved, of every broker — the live list's when
 *  it holds every broker, else the brokers' own counts summed (the stream follows one broker). */
export function liveCount(live) {
  if (live.broker == null || !brokers) {
    return (live.situations || []).filter((s) => s.status !== "resolved").length;
  }
  return brokers.reduce((n, b) => n + (b.live || 0), 0);
}

/** The sidebar's line for each broker, under *Brokers*, and under the one open its screens. */
export function brokerEntries(list, activeHref) {
  const out = [];
  for (const b of list || []) {
    const href = `#/brokers/${b.id}`;
    out.push({ key: `broker-${b.id}`, href, label: b.name, icon: brokerIcon(b),
      state: b.enabled ? null : "switched off", badge: b.live, noun: "live situations",
      tone: b.enabled ? null : "is-off" });
    if (activeHref === href || String(activeHref || "").startsWith(`${href}/`)) {
      for (const [part, label, icon] of partsFor()) {
        out.push({ key: `broker-${b.id}-${part}`, href: `${href}/${part}`, label, icon, deep: true });
      }
    }
  }
  return out;
}

/** `path` with `broker_id` added when a broker is named: one network's answer (ADR #496). */
export function withBroker(path, brokerId) {
  if (brokerId == null) return path;
  return `${path}${path.includes("?") ? "&" : "?"}broker_id=${brokerId}`;
}
