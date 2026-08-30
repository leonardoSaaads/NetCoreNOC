/* The theme, persisted in a cookie.
 *
 * ## Why a cookie and not `localStorage` (ADR #172, draft §9)
 *
 * `tests/test_security_ui.py` asserts `"localStorage" not in app_js`. That guard is F2's
 * remediation and its value is that it is an **absolute**: a first carve-out turns it into a
 * judgement call on every future diff. So the preference goes to a cookie —
 * `SameSite=Strict`, deliberately **not** `HttpOnly` because the client has to read it, carrying
 * a name from a closed set and nothing else. Never a user id, never a token, never a blob.
 *
 * A value outside the closed set is discarded and the preference falls back to the system
 * setting, so **a hostile cookie can at worst select a supported theme.** That is the whole of
 * this cookie's threat model and it is why the value is validated on read rather than trusted.
 *
 * ## The flash of the wrong theme, stated honestly
 *
 * A module script is deferred, so between first paint and this module running, the page is in
 * whatever `prefers-color-scheme` says. If the cookie disagrees with the system setting, the
 * operator sees one frame of the other theme. The usual fix is a tiny inline script in `<head>`,
 * and the CSP forbids inline scripts — correctly. This is a real, visible, unfixed cost of
 * principle 6 and `docs/security/SECURITY-REVIEW-0.13.0.md` records it rather than hiding it.
 */

const THEME_COOKIE = "ncn_theme";

/** The closed set. Anything else is not a preference, it is noise, and is discarded. */
export const THEMES = ["dark", "light", "system"];

function readCookie(name) {
  for (const pair of String(globalThis.document.cookie || "").split(";")) {
    const at = pair.indexOf("=");
    if (at === -1) continue;
    if (pair.slice(0, at).trim() === name) return pair.slice(at + 1).trim();
  }
  return null;
}

function writeCookie(name, value) {
  // One year. `SameSite=Strict` so it never travels on a cross-site navigation; `Path=/` because
  // the console is served from the root. No `Secure` flag: the appliance is routinely deployed on
  // plain HTTP inside a management network, and a cookie that silently failed to persist there
  // would be worse than one that carries a theme name in the clear.
  globalThis.document.cookie =
    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Strict`;
}

function validated(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

export function theme() {
  return validated(readCookie(THEME_COOKIE), THEMES, "system");
}

/**
 * Push the current preferences onto the document root.
 *
 * `data-theme` is absent for "system", which is what lets the stylesheet's
 * `@media (prefers-color-scheme: …)` keep working: an explicit attribute wins, and no attribute
 * means the operating system decides. The CSS is written so both directions are covered.
 */
export function apply() {
  const root = globalThis.document.documentElement;
  if (!root) return;
  const chosen = theme();
  if (chosen === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", chosen);
}

export function setTheme(value) {
  writeCookie(THEME_COOKIE, validated(value, THEMES, "system"));
  apply();
}

/**
 * What the operator is actually LOOKING at. "system" is not an appearance; it is a deferral, and
 * it resolves through the OS setting to one of the two that are.
 *
 * Guarded because the DOM harness has no `matchMedia`: absent it, "system" reads as light, which
 * is the same answer the stylesheet gives when no `prefers-color-scheme` matches.
 */
export function effective(chosen = theme()) {
  if (chosen !== "system") return chosen;
  const mq = globalThis.matchMedia && globalThis.matchMedia("(prefers-color-scheme: dark)");
  return mq && mq.matches ? "dark" : "light";
}

/**
 * The next theme is **whichever one the operator is not looking at** (F87).
 *
 * This cycled `dark -> light -> system -> dark`: three states through a control that can only
 * ever show two appearances. Whenever `system` resolved to the state next to it in the ring — and
 * it always resolves to one of them — one click in three changed nothing on screen. Measured in
 * Chromium with `prefers-color-scheme: light`: click 1 dark, click 2 light, **click 3 light
 * again**, and an operator reasonably reports that switching takes two clicks.
 *
 * No ordering of three states over two appearances avoids that, so the control is now a toggle and
 * `system` is what it is: the default before anyone has chosen, still the value in `THEMES`, still
 * what an absent cookie means, and still drawn with its own icon until the first click. What it is
 * no longer is a stop on the ring. Returning to it means clearing `ncn_theme`; a control that
 * offers three states needs a menu, and that is a design decision, not a bug fix.
 */
export function nextTheme(from = theme()) {
  return effective(from) === "dark" ? "light" : "dark";
}
