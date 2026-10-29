/* The NOC's agents, as the sidebar lists them (ADR #468) — and what the caller holds in the NOC
 * beyond their role (ADR #470).
 *
 * Paperclip's sidebar lists a company's agents by name, each with its state; the NOC plane's does
 * the same, under its *Agents* heading. The list is read here, once for the whole console, while
 * the NOC plane is on screen and the principal may read agents (`agents.read`) — never otherwise,
 * so a screen that cannot use it never asks for it. Twenty seconds between reads: a run takes
 * minutes, and the agent's own page reads its state more often while it is open.
 *
 * A screen that changes an agent (hiring, pausing, a new manager) calls `refreshRoster()`, so the
 * sidebar says what the screen just did without waiting for the next read.
 *
 * **The NOC grants** (`/api/noc/access`) are read with it: custodian (the vault), lead (the KPIs
 * and the playbooks) and agent. The sidebar offers a lead's screens to leads only (`hiddenViews`),
 * and those screens show their closed state from the same answer — while every route still
 * decides on its own, so hiding is a courtesy, never the control.
 */

import { get } from "./api.js";
import { can } from "./session.js";

const POLL_MS = 20000;

let agents = null;
let access = null;
let timer = null;
const listeners = new Set();
/** The screens a NOC grant opens, and the grant (ADR #470). */
const GATED = { kpis: "lead", playbooks: "lead" };

/** The caller's NOC grants as last read, or null before the first read. */
export function nocAccess() { return access; }

/** The grants, read now when they have not been yet. */
export async function readNocAccess() {
  if (access === null && can("cases.read")) {
    try { access = await get("/api/noc/access"); } catch { /* the screen says it cannot tell */ }
  }
  return access;
}

/** The sidebar entries to leave out: a lead's screens, until the grants say the caller is one. */
export function hiddenViews() {
  return Object.keys(GATED).filter((id) => !(access && access[GATED[id]]));
}

/** The agents last read (terminated ones are not listed), or null before the first read. */
export function roster() { return agents; }

/** Call `listener(agents)` after each read; returns the unsubscribe. */
export function onRoster(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function refreshRoster() {
  try { if (can("cases.read")) access = await get("/api/noc/access"); } catch { /* kept */ }
  if (!can("agents.read")) {
    for (const listener of listeners) listener(agents);
    return;
  }
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
  const wanted = Boolean(on) && (can("agents.read") || can("cases.read"));
  if (wanted && timer === null) {
    timer = setInterval(refreshRoster, POLL_MS);
    refreshRoster();
  } else if (!wanted && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}
