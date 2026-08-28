/* Load a vendored asset **when the screen that needs it mounts** (DECISIONS #228).
 *
 * `index.html` loaded d3 as a classic script on every page load: **279 706 bytes on all seventeen
 * screens**, twenty-two times the two framework assets combined, for the two that draw with it.
 * The v0.13.0 decision to keep d3 stands — writing a force layout is not a repair release's work —
 * but nothing ever decided that every screen should pay for it.
 *
 * ## Why a `<script>` element and not `import()`
 *
 * `d3.v7.min.js` is a **UMD** bundle: imported as an ES module it exports nothing and defines no
 * global. The alternatives are a bare specifier (an import map, which is an inline script the CSP
 * forbids) or vendoring a second copy of the same bytes in a different module format. Appending a
 * same-origin `<script src>` is neither: `script-src 'self'` permits it, no inline script is
 * introduced, and the bytes served are the bytes `CHECKSUMS.txt` pins.
 *
 * ## One load, whatever asks
 *
 * The promise is cached, so mounting the graph and then the timeline fetches once, and a view that
 * mounts twice does not race itself. A failure resolves rather than rejects — the caller checks
 * `globalThis.d3` and renders its own empty state, which is what it already did on a browser that
 * had not finished the classic script.
 *
 * In the DOM harness `globalThis.d3` is the recording double and is already present, so this
 * returns immediately and never touches the document: the harness's substitution keeps working
 * without knowing this file exists.
 */

const pending = new Map();

export function loadVendorScript(src) {
  if (pending.has(src)) return pending.get(src);
  const promise = new Promise((resolve) => {
    const document = globalThis.document;
    if (!document || !document.createElement) { resolve(false); return; }
    const script = document.createElement("script");
    script.src = src;
    script.addEventListener("load", () => resolve(true));
    script.addEventListener("error", () => resolve(false));
    document.head.appendChild(script);
  });
  pending.set(src, promise);
  return promise;
}

/** Resolve once `globalThis.d3` exists, or once we know it will not. */
export function d3Ready() {
  if (globalThis.d3) return Promise.resolve(true);
  return loadVendorScript("/vendor/d3.v7.min.js").then(() => Boolean(globalThis.d3));
}
