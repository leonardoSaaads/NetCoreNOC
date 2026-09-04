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
import { Loading, Empty, Failed } from "../widgets.js";
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
      live: store.get(),
      filter: "",
      status: "new",
      // null = not searching, the live list is what is rendered. An array = a server answer.
      results: null,
      searching: false,
      searchError: null,
    };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    // A deep link (#/situations/12) opens that card, which is what makes a situation shareable
    // during an incident (draft §2.4, §3).
    const deepLink = Number(this.props.params[0]);
    if (deepLink) this.open(deepLink);
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

  /** Re-fetch a card the operator has just changed, and re-freeze the hold on the new payload.
   *
   * A gesture the operator made is the one case where the held card SHOULD move: they changed it,
   * so showing them what they changed it to is not a state change arriving underneath a click. The
   * hold's whole purpose is that an update they did not ask for cannot move the grouping they are
   * judging (ADR #173), and `refreshHeld` is the existing name for exactly this — "the card was
   * re-fetched deliberately (the operator asked)". */
  async reopen(sid) {
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
    // A search answer is a snapshot, so a gesture made inside one has to be asked for again or the
    // row beside the card would keep the status, the name and the count it had before the gesture.
    this.refresh();
  }

  async open(sid) {
    if (store.isExpanded(sid)) { store.collapse(sid); return; }
    // Expand FIRST with no payload so the card shows its own loading state in place, then hold
    // the payload when it lands. v0.7.4 emptied the container before the round trip and a click
    // in that window hit nothing.
    store.expand(sid, null);
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
  }

  render(_props, { live, filter, status, results, searching, searchError }) {
    const query = filter.trim();
    // A search answers with the rows the SERVER matched; without one, the live list, filtered by
    // the tab. The id is still matched here, and only here: `#12` is a fact about the console's
    // own rendering, not about anything the server stores.
    const rows = query
      ? results || []
      : (live.situations || []).filter((s) => !status || s.status === status);

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
            onClick=${() => this.setState({ status: value }, () => this.refresh())}>${label}</button>`)}
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

