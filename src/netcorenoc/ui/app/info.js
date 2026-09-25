/* The info icon: **one component, used everywhere an explanation is genuinely needed** (v0.22.0).
 *
 * The console explains itself with layout, defaults and affordances; a sentence that is still
 * needed lives here, behind a small `i`, instead of inline above the thing it explains.
 *
 * Three properties, each a rule of this release rather than a nicety:
 *
 *   * **keyboard-reachable** — the opener is a `<button>`, so Tab reaches it and Enter/Space open
 *     it; Escape, a second press, or a click elsewhere close it;
 *   * **the text is in the accessibility tree while closed** — it is rendered always, clipped rather
 *     than removed, and the button points at it with `aria-describedby`, so a screen reader hears
 *     the explanation on focus without opening anything;
 *   * **no hover-only content** — a touch screen has no hover, and a phone is where the field
 *     engineer is.
 */

import { html, Component, cx } from "./dom.js";

let serial = 0;

export class InfoTip extends Component {
  constructor(props) {
    super(props);
    serial += 1;
    this.id = `info-${serial}`;
    this.state = { open: false };
    this.root = null;
    this.onDocument = this.onDocument.bind(this);
    this.onKey = this.onKey.bind(this);
  }

  componentDidMount() {
    globalThis.document.addEventListener("click", this.onDocument, true);
    globalThis.document.addEventListener("keydown", this.onKey);
  }

  componentWillUnmount() {
    globalThis.document.removeEventListener("click", this.onDocument, true);
    globalThis.document.removeEventListener("keydown", this.onKey);
  }

  onDocument(event) {
    if (this.state.open && this.root && !this.root.contains(event.target)) {
      this.setState({ open: false });
    }
  }

  onKey(event) {
    if (event.key === "Escape" && this.state.open) this.setState({ open: false });
  }

  render({ label, children }, { open }) {
    return html`<span class="info" ref=${(node) => { this.root = node; }}>
      <button type="button" class="info-open" aria-label=${label || "More about this"}
              aria-expanded=${open ? "true" : "false"} aria-describedby=${this.id}
              onClick=${() => this.setState({ open: !open })}>
        <span aria-hidden="true">i</span>
      </button>
      <span id=${this.id} role="note" class=${cx("info-text", open && "info-shown")}>
        ${children}
      </span>
    </span>`;
  }
}
