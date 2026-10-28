/* The NOC's agents, as the sidebar lists them (ADR #468).
 *
 * Paperclip's sidebar lists a company's agents by name, each with its state; the NOC plane's does
 * the same, under its *Agents* heading. The list is read here, once for the whole console, while
 * the NOC plane is on screen and the principal may read agents (`agents.read`) — never otherwise,
 * so a screen that cannot use it never asks for it. Twenty seconds between reads: a run takes
 * minutes, and the agent's own page reads its state more often while it is open.
 *
 * A screen that changes an agent (hiring, pausing, a new manager) calls `refreshRoster()`, so the
 * sidebar says what the screen just did without waiting for the next read.
 */

import { get } from "./api.js";
import { can } from "./session.js";

const POLL_MS = 20000;

let agents = null;
let timer = null;
const listeners = new Set();

/** The agents last read (terminated ones are not listed), or null before the first read. */
export function roster() { return agents; }

/** Call `listener(agents)` after each read; returns the unsubscribe. */
export function onRoster(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function refreshRoster() {
  if (!can("agents.read")) return;
  try {
    const data = await get("/api/noc/agents");
    const order = orgOrder(data.org);
    agents = data.agents.filter((a) => a.status !== "terminated")
      .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    for (const listener of listeners) listener(agents);
  } catch { /* the list stays as it was; the next read tries again */ }
}

/** The agents in the order of the organization: each manager before its reports. */
export function orgOrder(tree) {
  const order = [];
  const walk = (nodes) => { for (const n of nodes) { order.push(n.id); walk(n.reports); } };
  walk(tree || []);
  return order;
}

/** Read while `on` (the NOC plane is shown), and stop reading when it is not. */
export function watchRoster(on) {
  const wanted = Boolean(on) && can("agents.read");
  if (wanted && timer === null) {
    timer = setInterval(refreshRoster, POLL_MS);
    refreshRoster();
  } else if (!wanted && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}
