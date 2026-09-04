/* The two-region shell: sidebar and work area.
 *
 * ## It was three regions until v0.15.2, and the third was empty on every screen
 *
 * `#context` was a 320-pixel detail panel that **no view ever wrote to**: `setContext` was
 * imported by `situations.js` and never called, so all seventeen screens rendered
 * *"Select something to see its detail here."* permanently — measured in a browser as three
 * roles, and hidden outright below 760 px. `registry.js` states the rule it broke: nothing in
 * this console is a placeholder. Removal was chosen over completing it because the facts a
 * selection would show are already in the expanded card, in place (DECISIONS #219).
 *
 * ## The `#sidebar` collision, resolved (draft §1.1, §V.1)
 *
 * v0.12.0's `<div id="sidebar">` was the **work area** — the column holding the ten panels — and
 * naming it `sidebar` was already confusing before this release added actual sidebar navigation.
 * The rename is deliberate and total:
 *
 *   * `#nav`     — the navigation sidebar (new; `sidebar.js`)
 *   * `#work`    — the work area, which is what `#sidebar` used to be
 *
 * Nothing is called `sidebar` any more, so no guard can match the old id by accident and report
 * that it found navigation. `tests/domharness/selftest.mjs` and `tests/test_ui_invariants.py`
 * were rewritten to the new selectors, and the rule draft §1.1 sets — that the assertion count
 * may not go down during a selector rename — is checked by `test_ui_invariants` itself.
 *
 * ## Focus management on route change (§12.6's floor)
 *
 * When the route changes, focus moves to the work area's heading. Without this a keyboard
 * operator activates a nav item and their focus is still in the nav, so the next Tab walks the
 * rest of the navigation rather than entering the screen they just asked for. The heading is
 * `tabindex="-1"` — focusable programmatically, never a tab stop — which is the standard way to
 * do this without adding a stop nobody wants.
 */

import { html, Component, cx } from "./dom.js";
import { Sidebar } from "./sidebar.js";
import { Refused, Unknown } from "./widgets.js";
import { Icon } from "./icons.js";
import { resolve, navigate, startRouting, currentFragment } from "./router.js";
import { session, scopeSummary } from "./session.js";
import { theme, setTheme, nextTheme } from "./theme.js";
import * as store from "./store.js";
import { plural } from "./format.js";

export class Shell extends Component {
  constructor(props) {
    super(props);
    this.state = { fragment: currentFragment(), live: store.get() };
    this.headingRef = null;
    this.lastViewId = null;
  }

  componentDidMount() {
    this.stopRouting = startRouting((fragment) => this.setState({ fragment }));
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
  }

  componentWillUnmount() {
    if (this.stopRouting) this.stopRouting();
    if (this.unsubscribe) this.unsubscribe();
  }

  componentDidUpdate() {
    const decision = this.decision();
    const viewId = decision.kind === "view" ? decision.view.id : `!${decision.kind}`;
    if (viewId !== this.lastViewId) {
      this.lastViewId = viewId;
      if (this.headingRef && this.headingRef.focus) this.headingRef.focus();
    }
  }

  decision() {
    const active = session();
    return resolve(this.state.fragment, active ? active.capabilities : new Set());
  }

  render() {
    const active = session();
    const decision = this.decision();
    const live = this.state.live;
    const activeId = decision.kind === "view" ? decision.view.id : null;
    // The sidebar badge has always meant "situations that have not left", and it still does:
    // v0.16.0 split that population into `new` and `open`, so the filter names what it excludes
    // rather than what it includes (DECISIONS #254).
    const counts = {
      situations: (live.situations || []).filter((s) => s.status !== "resolved").length,
    };

    return html`<div id="app" class="shell">
      <${Sidebar} capabilities=${active.capabilities} activeId=${activeId}
                  counts=${counts} onNavigate=${navigate} />
      <div class="main">
        <${TopBar} live=${live} onSignOut=${this.props.onSignOut} />
        <${Banners} stats=${live.stats} />
        <main id="work" class="work" tabindex="-1">
          <${WorkHeading} decision=${decision}
                          headingRef=${(node) => { this.headingRef = node; }} />
          ${this.body(decision)}
        </main>
      </div>
    </div>`;
  }

  body(decision) {
    if (decision.kind === "unknown") return html`<${Unknown} viewId=${decision.viewId} />`;
    if (decision.kind === "refused") {
      return html`<${Refused} view=${decision.view} missing=${decision.missing} />`;
    }
    // THE ONE PLACE a decision becomes a mounted component. A second call site here would be a
    // second authorisation surface, which is the shape of F53 (see router.js).
    const View = decision.view.component;
    return html`<div class="view" data-view=${decision.view.id}>
      <${View} params=${decision.params} query=${decision.query} navigate=${navigate} />
    </div>`;
  }
}

function WorkHeading({ decision, headingRef }) {
  const title =
    decision.kind === "view" ? decision.view.label
      : decision.kind === "refused" ? decision.view.label
        : "Not found";
  const summary = decision.kind === "view" ? decision.view.summary : null;
  return html`<div class="work-heading">
    <h1 tabindex="-1" ref=${headingRef}>${title}</h1>
    ${summary ? html`<p class="work-summary">${summary}</p>` : null}
  </div>`;
}

/** Identity, scope, connection state and theme. Everything that is about the SESSION. */
function TopBar({ live, onSignOut }) {
  const active = session();
  const scope = scopeSummary();
  const connection = live.connection;
  return html`<header class="topbar">
    <div class="topbar-live">
      <span class=${cx("conn", `conn-${connection}`)} role="status"
            title=${CONNECTION_TITLE[connection]}>
        <span class="conn-dot" aria-hidden="true"></span>${CONNECTION_LABEL[connection]}
      </span>
      ${live.stats ? html`<${LiveChips} stats=${live.stats} />` : null}
    </div>
    <div class="topbar-who">
      ${scope ? html`<span class="badge badge-scope" title=${scope.title}>
        scoped: ${plural(scope.neCount, "NE", "NE")}</span>` : null}
      <span class="role-tag">${active.role}</span>
      <a class="who-name" href="#/account" title="Your account">${active.user}</a>
      <${ThemeButton} />
      <button type="button" onClick=${onSignOut}>Sign out</button>
    </div>
  </header>`;
}

const CONNECTION_LABEL = {
  connecting: "connecting", live: "live", polling: "polling", error: "no updates",
};
const CONNECTION_TITLE = {
  connecting: "Opening the update stream.",
  live: "Receiving server-sent updates.",
  polling: "The update stream is unavailable; falling back to polling every 2.5 s.",
  error: "No updates are arriving. What is on screen may be out of date.",
};
const THEME_ICON = { dark: "moon", light: "sun", system: "auto" };
const THEME_WORD = { dark: "dark", light: "light", system: "following the system" };

/**
 * The theme control, and it owns its own state (F87).
 *
 * It used to be markup inside `TopBar` reading `theme()` — a cookie — with a `forceRepaint()`
 * helper to make Preact notice. That helper called `store.setConnection(store.get().connection)`,
 * and `setConnection` returns early when the value is unchanged, so it **published nothing and
 * re-rendered nothing**. The icon and the label were frozen at whatever they said on first render:
 * measured in Chromium, the label read `Theme: system.` through six clicks while the page went
 * dark, light, dark, light. Only `data-theme` moved, because `apply()` writes it straight to the
 * document root and never goes through Preact at all.
 *
 * So the theme is held here instead of inferred from a cookie the framework cannot observe. A
 * class component because this build vendors Preact **core** without hooks — `hooks.module.js`
 * imports `"preact"`, a bare specifier needing an import map, and an import map is an inline
 * `<script>` that `script-src 'self'` forbids (ADR #174).
 */
class ThemeButton extends Component {
  constructor(props) {
    super(props);
    this.state = { chosen: theme() };
  }

  render(_props, { chosen }) {
    const next = nextTheme(chosen);
    // Both halves stated, because the icon shows the state and the click changes it, and a label
    // carrying only one of those leaves a screen-reader user guessing which it is.
    const said = `Theme: ${THEME_WORD[chosen]}. Switch to ${THEME_WORD[next]}.`;
    return html`<button type="button" class="icon" title=${said} aria-label=${said}
            onClick=${() => { setTheme(next); this.setState({ chosen: theme() }); }}>
      <${Icon} name=${THEME_ICON[chosen]} />
    </button>`;
  }
}

function LiveChips({ stats }) {
  const chips = [
    ["devices", stats.devices],
    ["classes", stats.classes],
    ["active alarms", stats.active_alarms],
    ["open situations", stats.open_situations],
  ];
  return html`<span class="chips">${chips.map(([label, value]) => html`
    <span class="chip" key=${label}><b>${value}</b>${label}</span>`)}</span>`;
}

/**
 * The two banners that must interrupt whatever screen the operator is on.
 *
 * An ingest gap means traps are being dropped **right now** and is the single most operationally
 * urgent thing this appliance can say, so it is not a chip on one screen — it is above the work
 * area on every screen. Unchanged in meaning from v0.12.0; moved so it is not tied to Situations.
 */
function Banners({ stats }) {
  if (!stats) return null;
  const openGaps = stats.open_ingest_gaps || [];
  const warnings = stats.warnings || [];
  return html`<div class="banners">
    ${openGaps.length ? html`<div class="banner banner-urgent" role="alert">
      <b>Ingest gap — dropping traps now.</b>
      ${` ${openGaps.reduce((n, g) => n + (g.dropped || 0), 0)} event(s) lost `}
      (${[...new Set(openGaps.map((g) => g.reason))].join(", ")}).
    </div>` : null}
    ${warnings.length ? html`<div class="banner banner-warn" role="status">
      <${Icon} name="warn" />${" "}${warnings.join("  •  ")}
    </div>` : null}
  </div>`;
}
