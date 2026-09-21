/* What a situation IS and HAS BEEN: its operator name, its history, how it ended.
 *
 * The three gestures that change **which alarms are in it** left for
 * `views/parts/restructure.js` in v0.20.0, at the module graph's ceiling and on the seam this
 * file's own title drew — *"move, merge, split, name"* — because a name is not a restructuring.
 * What is here writes no training row and is refused by no 409: naming a situation asserts
 * nothing about its grouping, and the history is a read.
 *
 * The `id` remains the identity. The heading still says `#12`, the permalink is unchanged, and a
 * name is never a key.
 */

import { html, Component } from "../../dom.js";
import { age, percent, timeTitle } from "../../format.js";

/**
 * The operator's own name for a situation.
 *
 * Written to a **different column** from the name the server derives: a derived name is a
 * projection of membership and evidence of nothing, an operator's name is a label and carries
 * provenance. Clearing the field withdraws the label and the derived name shows through again.
 *
 * The `id` remains the identity. The heading still says `#12`, the permalink is unchanged, and a
 * name is never a key.
 */
export class NameField extends Component {
  constructor(props) {
    super(props);
    this.state = { draft: props.operatorName || "", busy: false, outcome: null };
  }

  async save() {
    const { sid, post, onDone } = this.props;
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    const name = this.state.draft.trim();
    try {
      await post(`/api/situations/${sid}/name`, { name: name || null });
      this.setState({ busy: false, outcome: { ok: true, cleared: !name } });
      onDone();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  render({ derivedName }, { draft, busy, outcome }) {
    // **The placeholder IS the explanation** (v0.20.0). A 24-word sentence under the field said
    // that the greyed-out name in the field is the one the appliance derived — which the field
    // was already showing. The fact that survives is *where that name comes from*, and a title
    // is where a fact that only some readers want belongs.
    return html`<section class="lifecycle-name">
      <label for="lcName">Name this situation</label>
      <input id="lcName" type="text" maxlength="120" value=${draft} disabled=${busy}
             placeholder=${derivedName || "#id"} title=${NAME_TITLE}
             onInput=${(e) => this.setState({ draft: e.target.value })} />
      <button type="button" disabled=${busy} onClick=${() => this.save()}>Save</button>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="status">
        ${outcome.ok
          ? (outcome.cleared ? "Name withdrawn." : "Named.")
          : `Not saved — ${outcome.error.detail || outcome.error.message}`}
      </p>` : null}
    </section>`;
  }
}

const NAME_TITLE =
  "Your own name for this situation. With none, the greyed name shown here is the one the " +
  "appliance derives from the members, and it is recomputed when they change. The id above " +
  "stays the identity either way; a name is never a key.";

/* What has been done to this situation, and by whom.
 *
 * Five columns and no more, because the server sends five: the row also carries member digests and
 * a peer situation id, and a scoped reader must not learn either from a history panel. See
 * `store/situation_events.py::situation_events`.
 *
 * **v0.16.1: `by user:2` became `by alice`, where the server was willing to say so.** The actor is
 * a principal reference — correct, unforgeable, and unhelpful to a human reading their own work.
 * `actor_name` is the username *if the account still exists* and is withheld from a viewer by
 * `FIELD_RULES`, so this renders whichever the server sent and never guesses: a deleted account
 * and a service token both fall back to the reference, which is the honest answer rather than
 * "unknown", because the reference is still exactly who did it.
 *
 * ## v0.16.4, Bug 2: `by admin` and `2m` were one word, and the word looked like a counter
 *
 * The maintainer reported that *"`admin2` became `admin3`"*. Nobody was renaming anything: the row
 * rendered `by admin` immediately followed by the age, so `admin` + `2m` read as `admin2m` and, a
 * minute later, as `admin3m`. Two things were wrong and each needed its own repair, because each
 * is invisible to the thing that would have caught the other.
 *
 *   * **The layout.** `.age` carries `margin-left: auto`, and `style.css` had no `.history-list`
 *     rule at all — so the `<li>` was `display: list-item`, the auto margin did nothing, and the
 *     age sat against the name with up to 1 002 px of empty row beside it. The stylesheet now gives
 *     the row a flex context; measured, the age moved to the right edge at all three widths.
 *   * **The text.** A separator in CSS is not a separator in `textContent`, so a screen reader, a
 *     copy-paste and the DOM harness all still read `edt1s` after the layout was fixed. The
 *     explicit `${" "}` below is what separates the runs *as text*; a whitespace-only text node is
 *     not rendered as a flex item, so it costs nothing visually.
 *
 * The second half is why this is not a one-line CSS commit. Appendix B's blind spot is exactly
 * *"the DOM harness cannot see whitespace"*, and half a repair reads as a whole one until the other
 * half is measured. */
export function History({ events }) {
  return html`<section class="history">
    <h3>What has been done to this situation</h3>
    <ol class="history-list">
      ${events.map((e, index) => html`<li key=${index}>
        <span class="history-kind">${e.kind.replace("_", " ")}</span>
        ${e.actor ? html`<span class="muted" title=${e.actor_name ? e.actor : ACTOR_TITLE}
                         >${" by "}${e.actor_name || e.actor}</span>` : null}
        ${e.confidence != null
          ? html`<span class="muted">${` at ${percent(e.confidence)} confidence`}</span>` : null}
        ${" "}<span class="age" title=${timeTitle(e.at)}>${age(e.at)}</span>
      </li>`)}
    </ol>
  </section>`;
}

/** Shown where the console has only the reference: an account that is gone, a token, or a viewer
 * whose responses withhold the name. Never "unknown" — the reference IS who did it. */
const ACTOR_TITLE =
  "The principal that made this change. A name is shown where the account still exists and your " +
  "role is shown it; otherwise the reference stands, and it is the record either way.";

/** What a resolved situation says about **why** it left. */
export const RESOLUTION_TEXT = {
  operator: "an operator closed it",
  self_cleared: "every alarm cleared — the network fixed it",
  idle: "nobody looked at it, and it timed out",
  merged: "it was merged into another situation",
  manual_clear: "an operator hand-cleared the last active alarm",
  unattributed: "it closed before this appliance recorded why",
};
