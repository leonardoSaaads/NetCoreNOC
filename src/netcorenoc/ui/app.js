/* NetCoreNOC console — entry point.
 *
 * This file boots and does nothing else. It resolves a session, mounts either the sign-in card or
 * the shell, and opens the update stream. Every decision about *what a principal may see* is in
 * `app/router.js` and `app/registry.js`; every decision about *what a screen shows* is in the view
 * module that owns it.
 *
 * ## What replaced what
 *
 * v0.12.0's `ui/app.js` was 52 738 bytes with 55 top-level functions and no test executed it.
 * The replacement is a module graph: this entry point, fourteen infrastructure modules and
 * seventeen views, each under the 400-line guard `tests/test_architecture.py` now applies to
 * JavaScript as well as Python. **Replacing one 52 KB file with one 52 KB file in a new syntax
 * would have been failing while appearing to succeed** (Part XI), so the shape is the deliverable
 * and the framework is only what makes the shape affordable.
 *
 * No build step, no npm, no lockfile, no bundle. `type="module"` and relative specifiers; the
 * source a maintainer edits is the source the browser runs.
 */

import { html, render } from "./app/dom.js";
import { get, post, setUnauthenticatedHandler } from "./app/api.js";
import { setSession, session } from "./app/session.js";
import { Login } from "./app/login.js";
import { Shell } from "./app/shell.js";
import * as store from "./app/store.js";
import { apply as applyTheme } from "./app/theme.js";
import { currentFragment } from "./app/router.js";

const POLL_INTERVAL_MS = 2500;
const ROOT_ID = "root";

let stream = null;
let pollTimer = null;

function root() { return globalThis.document.getElementById(ROOT_ID); }

/** Mount whatever the current session says should be on screen. */
function draw() {
  const active = session();
  if (!active) {
    render(html`<${Login} onSignedIn=${signedIn} mustChange=${false} />`, root());
    return;
  }
  if (active.mustChangePassword) {
    render(html`<${Login} onSignedIn=${signedIn} mustChange=${true} />`, root());
    return;
  }
  render(html`<${Shell} onSignOut=${signOut} />`, root());
}

function signedIn(me) {
  setSession(me);
  if (session().mustChangePassword) { draw(); return; }
  draw();
  startStream();
  poll();
}

async function signOut() {
  try { await post("/api/logout", {}); } catch { /* the session is going away regardless */ }
  stopStream();
  store.reset();
  setSession(null);
  // Clear the fragment so a signed-out browser does not sit on a deep link it can no longer
  // resolve; the next sign-in then lands on the default view rather than on a refusal.
  globalThis.location.hash = "";
  draw();
}

/* ---------- live updates: SSE primary, polling fallback ---------- */

function startStream() {
  stopStream();
  try {
    stream = new globalThis.EventSource("/api/events");
    stream.addEventListener("update", (event) => {
      try { store.applyUpdate(JSON.parse(event.data)); store.setConnection("live"); }
      catch { /* a malformed frame is not a reason to tear down the stream */ }
    });
    stream.onerror = () => { stopStream(); startPolling(); };
  } catch {
    startPolling();
  }
}

function stopStream() {
  if (stream) { stream.close(); stream = null; }
  if (pollTimer) { globalThis.clearInterval(pollTimer); pollTimer = null; }
}

function startPolling() {
  if (pollTimer) return;
  store.setConnection("polling");
  pollTimer = globalThis.setInterval(poll, POLL_INTERVAL_MS);
}

/**
 * One read of the three live things.
 *
 * Every screen renders from the store, so this is the only place the live triple is fetched, and
 * a screen that needs something else fetches it itself. That is what keeps the shared path
 * unchanged as views are added — principle 4's discipline applied to the client.
 */
async function poll() {
  if (!session()) return;
  try {
    const [stats, graph, situations] = await Promise.all([
      get("/api/stats"),
      get("/api/graph"),
      get("/api/situations?limit=50&status=open"),
    ]);
    store.applyUpdate({ stats, graph, situations });
    if (!stream) store.setConnection("polling");
  } catch {
    store.setConnection("error");
  }
}

/* ---------- boot ---------- */

setUnauthenticatedHandler(() => {
  stopStream();
  store.reset();
  setSession(null);
  draw();
});

export async function boot() {
  applyTheme();
  let me = null;
  // **The `catch` covers the request and nothing else.** Wrapping the render in it too would mean
  // that any error inside a screen sent the operator to the sign-in card — a console that
  // silently logs you out when a component throws, during the incident that made the component
  // throw. A render error must surface as an error, not as a session that appears to have ended.
  try {
    me = await get("/api/me");
  } catch {
    setSession(null);
    draw();
    return;
  }
  setSession(me);
  draw();
  if (session().mustChangePassword) return;
  startStream();
  await poll();
}

/** Exported for the harness, which awaits it rather than racing the module's own boot. */
export const booted = boot();

/** Read once so an unused import cannot hide a routing module that failed to load. */
void currentFragment;
