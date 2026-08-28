/* Hash routing, and **the structural repair of F53**.
 *
 * ## The finding this module exists to close
 *
 * `docs/security/SECURITY-REVIEW-0.12.0.md` §2 (F53), reproduced by execution in
 * `docs/gates/v0.13.0-phase-0.md` §3: v0.12.0's panel loaders had **no capability check**. A
 * viewer calling `renderPanel("audit")` issued no request — but only because `prunePanels` had
 * removed the container and `clear(null)` threw a `TypeError` before `api(...)` was reached.
 *
 * > *Before trusting a behaviour, ask which line of code is responsible for it — and if the
 * > answer is "an exception", it is not a guarantee.*
 *
 * Routing is precisely what arms that: a URL fragment can name any view. So the repair is not a
 * check bolted onto a router, it is where the check lives:
 *
 *   **`resolve()` returns a decision, and the decision is made before anything is constructed.**
 *
 * A view's component is only ever handed to the renderer when its capabilities have already been
 * resolved against `/api/me`'s set. A principal without them receives a `refused` decision
 * carrying a different component, so the real one is never mounted, never runs
 * `componentDidMount`, and therefore issues no request. **The zero is a decision, not a
 * dereference.**
 *
 * Two properties follow, and both are asserted rather than asserted-about:
 *
 *   * `resolve()` is a pure function of (fragment, capability set). A test can drive it with any
 *     capability set at all, without a DOM, without a network, and without a role that happens
 *     to exist — which is what makes the refusal path reachable from a test at all.
 *   * there is exactly one call site that turns a decision into a mounted component. A second
 *     one would be a second authorisation surface, which is the failure this file is about.
 */

import { canAll } from "./session.js";
import { VIEWS, DEFAULT_VIEW } from "./registry.js";

/** `#/situations/12?flt=open` -> `{ viewId: "situations", params: ["12"], query: URLSearchParams }` */
export function parseFragment(fragment) {
  const raw = String(fragment || "").replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?", 2);
  const segments = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
  return {
    viewId: segments[0] || DEFAULT_VIEW,
    params: segments.slice(1),
    query: new URLSearchParams(queryPart || ""),
  };
}

/**
 * Decide what a fragment means for the principal who is asking.
 *
 * Returns one of:
 *   `{ kind: "view",    view, params, query }`  — mount it
 *   `{ kind: "unknown", viewId }`               — no such view
 *   `{ kind: "refused", view, missing }`        — the view exists; this principal may not have it
 *
 * **Pure.** It touches no DOM, issues no request, and constructs no component. That is what lets
 * `tests/test_ui_invariants.py` drive every view at every capability set and assert the refusal
 * without needing a server that would refuse.
 */
export function resolve(fragment, capabilitySet) {
  const { viewId, params, query } = parseFragment(fragment);
  const view = VIEWS.find((entry) => entry.id === viewId);
  if (!view) return { kind: "unknown", viewId };
  const required = requirementsOf(view);
  const missing = required.filter((capability) => !capabilitySet.has(capability));
  if (missing.length) return { kind: "refused", view, missing };
  return { kind: "view", view, params, query };
}

/** Every capability a view needs, always as a list, so one shape covers both declarations. */
export function requirementsOf(view) {
  const declared = view.capability;
  if (declared == null) return [];
  return Array.isArray(declared) ? declared : [declared];
}

/**
 * The views this principal may reach, in registry order.
 *
 * The sidebar renders from this, so navigation and authorisation read the SAME table. v0.12.0
 * kept a `TABS` array for navigation and a `prunePanels` loop for the DOM, and the two agreeing
 * was a convention. Here there is one list and the sidebar cannot offer what `resolve()` would
 * refuse, because both call `canAll` on the same declaration.
 */
export function reachableViews(capabilitySet) {
  return VIEWS.filter((view) => {
    if (view.hidden) return false;
    return requirementsOf(view).every((capability) => capabilitySet.has(capability));
  });
}

/* ---------- the browser-facing half ---------- */

export function currentFragment() {
  return globalThis.location.hash || "";
}

export function navigate(to) {
  globalThis.location.hash = to.startsWith("#") ? to : `#/${to.replace(/^\/+/, "")}`;
}

/** Register `onChange` for fragment changes and fire it once for the fragment already there. */
export function startRouting(onChange) {
  const handler = () => onChange(currentFragment());
  globalThis.window.addEventListener("hashchange", handler);
  handler();
  return () => globalThis.window.removeEventListener("hashchange", handler);
}

/** `canAll` re-exported so a caller resolving a view never reaches for a second predicate. */
export { canAll };
