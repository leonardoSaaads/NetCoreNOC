/* The live-data store: what the SSE stream and the poll both write into, and what the shell
 * renders from.
 *
 * ## The held card, kept — and why the reconciler did not remove the need for it
 *
 * v0.7.5's defect was that a server-sent update destroyed the card an operator was mid-gesture
 * on, so a click landed on a render they had never read. Preact's diff means the DOM node now
 * survives an update by construction — the button is the same object across re-renders, which
 * `tests/test_ui_invariants.py` asserts.
 *
 * **That is not sufficient and mistaking it for sufficient would reintroduce the defect.** The
 * node surviving is not the same as the *grouping* surviving. An operator who has ticked members
 * 2 and 4 of an eight-member situation is asserting something about those two alarms; if an
 * update re-orders or re-members the situation underneath them, the node identity is intact and
 * the meaning of the marks is not. So the payload behind an expanded card is HELD — frozen at
 * what the operator is looking at — exactly as v0.12.0 held the DOM subtree, and the staleness
 * marker is still the whole of the mitigation for that trade (ADR #173).
 *
 * Withheld updates are counted rather than discarded, so the marker can say how far behind the
 * card is instead of only that it is behind.
 */

const state = {
  stats: null,
  /** The derived trap rate, or null until two samples with a `receiver` block have arrived. */
  trapRate: null,
  graph: null,
  situations: [],
  connection: "connecting",   // connecting | live | polling | error
  /** sid -> the detail payload the operator is judging, frozen while the card is open. */
  held: new Map(),
  /** sid -> how many updates have been withheld from this card since it was expanded. */
  withheld: new Map(),
  expanded: new Set(),
};

const subscribers = new Set();

/* ---------- the trap rate, derived rather than served (DECISIONS #222) ---------- */

/** The previous `{ received, at }`. `/api/stats` serves a counter; a rate is two of them. */
let lastSample = null;

/**
 * Traps per second between the last two updates, with **the window it covers**.
 *
 * A rate with no window is a number nobody can act on: at a 2.5 s poll and a 30 s stream gap the
 * same figure means different things, and the operator cannot tell which they are looking at. So
 * `windowS` travels with it and the screen prints both.
 *
 * Three cases return null rather than a wrong number: the first sample (there is no window yet),
 * a response with no `receiver` block (the appliance was built without `extra_stats`), and a
 * counter that went **backwards**, which means the appliance restarted and the difference is not
 * a rate.
 */
function deriveRate(next) {
  const received = next && next.receiver ? next.receiver.received : null;
  if (received === null || !Number.isFinite(received)) { lastSample = null; return null; }
  const at = Date.now() / 1000;
  const previous = lastSample;
  lastSample = { received, at };
  if (!previous) return null;
  const windowS = at - previous.at;
  if (!(windowS > 0) || received < previous.received) return null;
  return { perSecond: (received - previous.received) / windowS, windowS };
}

export function get() { return state; }

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

function publish() {
  for (const fn of [...subscribers]) fn(state);
}

/** Apply a server-sent or polled update. Held cards keep the payload they were opened with. */
export function applyUpdate(update) {
  if (update.stats) {
    state.trapRate = deriveRate(update.stats);
    state.stats = update.stats;
  }
  if (update.graph) state.graph = update.graph;
  if (update.situations) state.situations = update.situations;
  for (const sid of state.held.keys()) {
    state.withheld.set(sid, (state.withheld.get(sid) ?? 0) + 1);
  }
  publish();
}

export function setConnection(status) {
  if (state.connection === status) return;
  state.connection = status;
  publish();
}

/* ---------- the held card ---------- */

export function isExpanded(sid) { return state.expanded.has(sid); }

export function expand(sid, detail) {
  state.expanded.add(sid);
  state.held.set(sid, detail);
  state.withheld.set(sid, 0);
  publish();
}

export function collapse(sid) {
  state.expanded.delete(sid);
  state.held.delete(sid);
  // The marker must go with the hold, not wait for the next update: a collapsed card carrying
  // "held while open" would assert something false until the next event arrived.
  state.withheld.delete(sid);
  publish();
}

/** The frozen payload for an open card, or null. */
export function heldDetail(sid) { return state.held.get(sid) ?? null; }

/** How many updates this card has withheld. 0 means open but not yet stale. */
export function withheldCount(sid) { return state.withheld.get(sid) ?? 0; }

/** The card was re-fetched deliberately (the operator asked): re-freeze on the new payload. */
export function refreshHeld(sid, detail) {
  if (!state.expanded.has(sid)) return;
  state.held.set(sid, detail);
  state.withheld.set(sid, 0);
  publish();
}

/** Sign-out and re-entry must not inherit a previous principal's open cards. */
export function reset() {
  state.stats = null;
  state.trapRate = null;
  lastSample = null;
  state.graph = null;
  state.situations = [];
  state.connection = "connecting";
  state.held.clear();
  state.withheld.clear();
  state.expanded.clear();
  publish();
}
