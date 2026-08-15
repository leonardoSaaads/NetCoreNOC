/* The rendering primitives, in one place, so every other module imports them from here.
 *
 * `htm` is a tagged template literal evaluated by the browser: the source a maintainer edits is
 * the source the browser runs. That is the whole of principle 6's content and the reason this
 * project can have a component model without acquiring a build step (ADR #171, #174).
 *
 * ## The escaping property, and why it is now structural (F1, invariant 4)
 *
 * v0.12.0's UI carried `esc()` and an `el({text})` discipline, and the property "no render path
 * turns operator-supplied text into markup" was a CONVENTION every one of 55 functions had to
 * keep. Here it is a property of the renderer: an interpolated value becomes a text node, never
 * markup, because that is what the diff does with it. The only way back to the old failure is
 * `dangerouslySetInnerHTML`, which is a single searchable token that no module uses and
 * `tests/test_security_ui.py` refuses.
 *
 * `esc()` survives anyway, for two reasons that are not decoration: it is the harness's proof of
 * execution (`domdriver.ESC_PROOF`), and it is still the right tool on the rare path that
 * composes a string *before* display — a `title` attribute assembled from several fields.
 */

import { h, render, Component, Fragment } from "../vendor/preact-10.29.8.module.js";
import htm from "../vendor/htm-3.1.1.module.js";

export const html = htm.bind(h);
export { h, render, Component, Fragment };

/**
 * Escape the five characters that can change the meaning of markup.
 *
 * Byte-identical in behaviour to v0.12.0's `esc()`, deliberately: `tests/domdriver.py` asserts
 * its output for `'<a href="x">'` as the harness's proof that the real UI evaluated, and that
 * constant has not moved.
 */
export function esc(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

/** Join class names, dropping falsy entries, so a conditional class is an expression. */
export function cx(...names) {
  return names.filter(Boolean).join(" ");
}
