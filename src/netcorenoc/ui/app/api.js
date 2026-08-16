/* The one place this UI talks to the server.
 *
 * Unchanged in contract from v0.12.0: cookie session, `X-NetCoreNOC-Client: ui` on every
 * mutation (the CSRF check in `api/perimeter.py` requires it), the server's own `detail` string
 * surfaced rather than a bare status code, and a 401 that returns the client to the sign-in
 * screen instead of leaving a half-authenticated view on screen.
 *
 * ## What is new, and why it is here rather than in the router
 *
 * `ApiError` carries `status` as well as `message`. v0.12.0 threw `Error(String(status))` and
 * every caller re-parsed it out of the text; a 403 and a 404 were indistinguishable to a screen
 * that wanted to say *"this was refused"* versus *"this is gone"*. The distinction matters most
 * on the write paths whose 404 is deliberately indistinguishable from a scope denial (F34/F37),
 * because those are exactly the ones a screen must NOT explain away.
 */

const MUTATION_HEADERS = { "X-NetCoreNOC-Client": "ui" };

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail || "";
  }
}

/** Invoked on any 401, wired by the entry point so this module depends on nothing. */
let onUnauthenticated = () => {};
export function setUnauthenticatedHandler(handler) { onUnauthenticated = handler; }

export async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (method !== "GET") Object.assign(headers, MUTATION_HEADERS);
  if (options.json !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
  });

  if (response.status === 401) {
    onUnauthenticated();
    throw new ApiError(401, "Your session has ended. Sign in again.");
  }
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail || ""; } catch { /* not JSON */ }
    throw new ApiError(response.status, detail);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

export const get = (path) => api(path);
export const post = (path, json) => api(path, { method: "POST", json });
export const del = (path) => api(path, { method: "DELETE" });

/**
 * Read several routes at once, reporting per-route outcomes instead of one rejection.
 *
 * A dashboard reads five things and a NOC needs the four that answered. v0.12.0's panels used
 * `Promise.all` inside a `try { } catch { return; }`, so one failing route rendered a **blank
 * screen with no explanation** — the single worst behaviour a monitoring console can have during
 * an incident, because a blank screen and a quiet network look identical.
 *
 * Returns `{ key: {ok, value, error} }`. The caller decides what a partial answer means; nothing
 * here decides it for them.
 */
export async function readAll(entries) {
  const keys = Object.keys(entries);
  const settled = await Promise.all(keys.map(async (key) => {
    try { return [key, { ok: true, value: await get(entries[key]) }]; }
    catch (error) { return [key, { ok: false, error }]; }
  }));
  return Object.fromEntries(settled);
}
