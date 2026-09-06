/* Situations — **the screen that justifies this release** (§IV.3).
 *
 * > The operator must be able to answer *"why did the system group these alarms?"* without
 * > leaving the screen.
 *
 * That is principle 2 and it is the product's whole claim. Every expanded card reaches the
 * per-term contributions that produced each link: the three named terms, their numbers, and the
 * threshold the sum had to clear. Not a score. The decomposition.
 *
 * **v0.16.1: the card moved to `views/parts/card.js`.** This module now owns *finding* a
 * situation — the list, the three tabs and the search — and that one owns *judging* it. The held
 * payload, the five gestures and the labelling contract all went with it, and its header carries
 * them. The split is not cosmetic: this file reached 411 lines when the search landed, over both
 * the 400-line rule and the module-graph guard, and a file over budget for a year had been two
 * things for a year.
 *
 * ## The search box asks the server (v0.16.1)
 *
 * It used to filter `` `#${s.id} ${s.status}` `` over the rows the stream had already pushed —
 * id and status only, over at most one page. An operator who had just named a situation *"fibre
 * cut, Ridgeway ring"* could not find it by that name, and nobody could find one by the device it
 * is on.
 *
 * A client-side filter cannot fix that: the device, the OID and the instance are on the DETAIL
 * payload, not on the list rows, and what is not loaded cannot be searched however clever the
 * predicate. So a non-empty box asks `GET /api/situations?q=`, where the match is a **query**
 * filter carrying the same scope predicate the listing does — and the result is rendered with a
 * marker saying it is a snapshot rather than the live list, because it is: a search answer the
 * stream keeps overwriting would be a list that changed under the operator's click, which is the
 * defect the held card exists for one level down.
 */

import { html, Component, cx } from "../dom.js";
import { SituationCard } from "./parts/card.js";
import { get } from "../api.js";
import { Loading, Empty, Failed, routeKey } from "../widgets.js";
import { plural } from "../format.js";
import * as store from "../store.js";

const SEARCH_DEBOUNCE_MS = 250;

export class Situations extends Component {
  constructor(props) {
    super(props);
    // v0.16.0: `new` is what the correlator creates and what an untriaged appliance is full of,
    // so it is the state this screen opens on. `open` would have shown an empty list on a working
    // appliance the moment this release shipped (DECISIONS #254).
    this.state = {
      // A deep-linked search: `#/situations?q=10.1.0.1`. The router has parsed a query string
      // since v0.13.0 and nothing used one; the graph's tables link here, so an operator reading
      // "this element alarms most" reaches its situations in one click rather than by retyping.
      filter: (props.query && props.query.get("q")) || "",
      live: store.get(),
      status: "new",
      // null = not searching, the live list is what is rendered. An array = a server answer.
      results: null,
      searching: false,
      searchError: null,
      // The cards this operator has gestured on since arriving. **DECISIONS #267.**
      pinned: new Set(),
    };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    /* A deep link (#/situations/12) opens that card, which is what makes a situation shareable
     * during an incident (draft §2.4, §3).
     *
     * **It is pinned as well, and that is F97.** This screen opens on the New tab (DECISIONS
     * #254), so a permalink to a situation in any other state expanded a card the list did not
     * contain and the operator was shown an unremarkable tab with nothing in it — no card, no
     * error, no explanation. Measured in the v0.16.1 live pass with a control: the same link to a
     * `new` situation rendered the card expanded; to an `open` one it rendered nothing at all.
     * The link is the operator asking for THIS situation, so the pin is exactly the right
     * mechanism — the card appears where they are, its badge says which state it is in, and
     * collapsing it or choosing a tab releases it like any other pin. */
    this.followDeepLink();
    if (this.state.filter.trim()) this.search(this.state.filter.trim());
  }

  /* **The permalink is honoured on every arrival, not only the first** (v0.16.4, F108).
   *
   * A hash change from `#/situations/38` to `#/situations/41` is a same-document navigation: the
   * router publishes the new fragment, its decision names this same view, and this component is
   * **not** remounted — so `componentDidMount` ran once and the deep link was read once. Measured:
   * the address bar said `#/situations/41` while the card for 38 was the one still open, with no
   * error and no empty state. Controls: the same address on a full load, and after leaving to
   * Overview and returning, both opened the right card.
   *
   * That is the case that happens during an incident — an operator already on this screen pastes a
   * colleague's link — and `card.js` calls the permalink *"shareable during the incident"*.
   *
   * The comparison is `Loader.routeKey`'s, imported rather than rewritten: *"did this route's
   * parameters change"* has one answer in this console and a second copy of it is how two screens
   * come to disagree about what a route change is.
   */
  componentDidUpdate(previous) {
    if (routeKey(previous.params) === routeKey(this.props.params)) return;
    this.followDeepLink();
  }

  /* A deep link (#/situations/12) opens that card, which is what makes a situation shareable
   * during an incident (draft §2.4, §3).
   *
   * **It is pinned as well, and that is F97.** This screen opens on the New tab (DECISIONS
   * #254), so a permalink to a situation in any other state expanded a card the list did not
   * contain and the operator was shown an unremarkable tab with nothing in it — no card, no
   * error, no explanation. Measured in the v0.16.1 live pass with a control: the same link to a
   * `new` situation rendered the card expanded; to an `open` one it rendered nothing at all.
   * The link is the operator asking for THIS situation, so the pin is exactly the right
   * mechanism — the card appears where they are, its badge says which state it is in, and
   * collapsing it or choosing a tab releases it like any other pin. */
  followDeepLink() {
    const sid = Number(this.props.params[0]);
    if (!sid) return;
    // The pin is unconditional and the expand is not, and the two must not be folded together.
    // `store.expanded` is module state that outlives this component, while `pinned` is reset by
    // the constructor — so on a remount a card can already be expanded and still be absent from
    // the tab's list. Skipping the pin because it was open left the operator on the New tab with
    // no card at all, which is F97's symptom reached by a different door. And `open` TOGGLES, so
    // calling it on a card already open would close the one they asked for.
    this.setState({ pinned: new Set(this.state.pinned).add(sid) });
    if (!store.isExpanded(sid)) this.open(sid);
  }

  componentWillUnmount() {
    if (this.unsubscribe) this.unsubscribe();
    if (this.timer) clearTimeout(this.timer);
  }

  /** Type, wait, ask. Debounced so a five-letter device name is one request rather than five. */
  onSearch(text) {
    this.setState({ filter: text });
    if (this.timer) clearTimeout(this.timer);
    const needle = text.trim();
    if (!needle) { this.setState({ results: null, searching: false, searchError: null }); return; }
    this.timer = setTimeout(() => this.search(needle), SEARCH_DEBOUNCE_MS);
  }

  /* Choosing a tab RELEASES every pin, and that is not the same act as gesturing.
   *
   * The pin exists so a card does not move out from under an operator who just acted on it
   * (DECISIONS #267). Picking a tab is the operator asking for a different list — found in the
   * v0.16.1 live pass, where a card pinned in "New" was still on screen after switching to
   * "Open", so it appeared in both. A courtesy that outlives the thing it was a courtesy about
   * is a second, private notion of state, which is exactly what #267 refused to build. */
  pickTab(value) {
    this.setState({ status: value, pinned: new Set() }, () => this.refresh());
  }

  /** Re-run the current search, which is what a gesture inside a search result needs. */
  refresh() {
    const needle = this.state.filter.trim();
    if (needle) this.search(needle);
  }

  async search(needle) {
    // The status tab travels with the query: a search inside "New" means "new AND matching", which
    // is what the tab above the list says it means.
    const status = this.state.status ? `&status=${encodeURIComponent(this.state.status)}` : "";
    this.setState({ searching: true, searchError: null });
    // `this.state.filter` is re-read on arrival rather than captured: two keystrokes in flight
    // must not let the slower answer overwrite the faster one.
    try {
      const rows = await get(`/api/situations?q=${encodeURIComponent(needle)}${status}`);
      if (this.state.filter.trim() === needle) this.setState({ results: rows, searching: false });
    } catch (error) {
      if (this.state.filter.trim() === needle) {
        this.setState({ searching: false, searchError: error, results: [] });
      }
    }
  }

  /* Re-fetch a card the operator just changed, re-freeze the hold, and **keep the card where they
   * are looking** (ADR #173, DECISIONS #267).
   *
   * A gesture the operator made is the one case where the held card SHOULD move: they changed it,
   * so showing them what they changed it to is not a state change arriving underneath a click. The
   * hold's whole purpose is that an update they did NOT ask for cannot move the grouping they are
   * judging, and `refreshHeld` is the existing name for exactly this.
   *
   * The pin is the same argument about the LIST. The first gesture promotes `new` to `open`
   * (DECISIONS #254), so the card leaves the tab it was opened from — the default tab, which on an
   * untriaged appliance is where an operator does most of their work. Measured in the v0.16.0 live
   * pass and recorded in that release's brief §5. The tab does not follow the card, because the tab
   * is what the operator chose; the card stays until they collapse it or pick a different tab, and
   * its badge still says `open`, so nothing is hidden. The pin lives nowhere: it is state on this
   * component for the length of a visit.
   */
  async reopen(sid) {
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
    this.setState({ pinned: new Set(this.state.pinned).add(sid) });
    // A search answer is a snapshot, so a gesture made inside one has to be asked for again or the
    // row beside the card would keep the status, the name and the count it had before the gesture.
    this.refresh();
  }

  async open(sid) {
    if (store.isExpanded(sid)) {
      // Collapsing releases the hold AND the pin: the card goes back to the tab it belongs to,
      // which is what makes the pin a courtesy rather than a second, private notion of state.
      store.collapse(sid);
      const pinned = new Set(this.state.pinned);
      pinned.delete(sid);
      this.setState({ pinned });
      return;
    }
    // Expand FIRST with no payload so the card shows its own loading state in place, then hold
    // the payload when it lands. v0.7.4 emptied the container before the round trip and a click
    // in that window hit nothing.
    store.expand(sid, null);
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
  }

  render(_props, { live, filter, status, results, searching, searchError, pinned }) {
    const query = filter.trim();
    // A search answers with the rows the SERVER matched; without one, the live list, filtered by
    // the tab — plus any card this operator has gestured on, which stays where they are looking
    // until they collapse it (DECISIONS #267).
    const rows = query
      ? results || []
      : (live.situations || []).filter(
          (s) => !status || s.status === status || (pinned.has(s.id) && store.isExpanded(s.id)),
        );

    return html`<div class="situations">
      <div class="filters" role="search">
        <label class="visually-hidden" for="fltText">Search situations</label>
        <input id="fltText" type="search" value=${filter}
               placeholder="search by name, device, OID or instance"
               onInput=${(e) => this.onSearch(e.target.value)} />
        <span class="filter-count">${searching ? "searching…" : plural(rows.length, "situation")}</span>
      </div>

      <div class="tabs" role="tablist" aria-label="Situation state">
        ${TABS.map(([value, label, hint]) => html`<button key=${value} type="button" role="tab"
            id=${`tab-${value || "any"}`}
            class=${cx("tab", status === value && "tab-on")}
            aria-selected=${status === value ? "true" : "false"}
            aria-controls="sits" title=${hint}
            onClick=${() => this.pickTab(value)}>${label}</button>`)}
      </div>

      ${query ? html`<p class="hint search-note">${SEARCH_NOTE}</p>` : null}
      ${searchError ? html`<${Failed} error=${searchError} what="the search"
                                      retry=${() => this.refresh()} />` : null}

      ${!query && live.situations === null ? html`<${Loading} label="Reading situations" />` : null}
      ${rows.length === 0 && !searching && !searchError ? html`<${Empty}
          title=${query || status !== "new"
            ? "No situations match this filter."
            : "The network is quiet."}
          will=${query
            ? "The search reads the operator's name, the derived name, the device, the trap OID " +
              "and the instance — and only the ones your account is shown. Clearing the box " +
              "returns to the live list."
            : status !== "new"
            ? "Clearing the filter will show everything the appliance currently holds."
            : "Situations appear here the moment two or more alarms correlate. Nothing has to be " +
              "configured first: the appliance learns alarm classes, network elements and their " +
              "affinities from the trap stream itself."}
          meanwhile=${query || status !== "new"
            ? null
            : "Point your devices' trap destination at this appliance. The Alarm classes screen " +
              "fills in as soon as the first trap arrives, before any grouping happens."} />` : null}

      <div id="sits" class="cards" role="tabpanel"
           aria-labelledby=${`tab-${status || "any"}`}>
        ${rows.map((s) => html`<${SituationCard} key=${s.id} situation=${s}
                                                 onToggle=${() => this.open(s.id)}
                                                 onChanged=${() => this.reopen(s.id)} />`)}
      </div>
    </div>`;
  }
}

/* The three states the schema now has, and the fourth entry that is not a state.
 *
 * `new` leads because it is what the correlator creates and what an untriaged appliance is full of
 * (DECISIONS #254). The titles say what each state MEANS rather than repeating its name: "open" and
 * "new" are not self-explanatory to somebody who has just been handed the console. */
const SEARCH_NOTE =
  "These are search results, not the live list: they were matched by the server when you typed " +
  "and they do not update on their own. The tab above narrows them. Clear the box to go back to " +
  "the live list.";

const TABS = [
  ["new", "New", "Formed by the correlator, and nobody has looked at it yet"],
  ["open", "Open", "An operator has touched it: judged, moved, merged, split or named it"],
  ["resolved", "Resolved", "It has left — and the card says why"],
  ["", "Any", "Every situation this appliance currently holds"],
];

