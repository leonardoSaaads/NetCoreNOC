/* Card 4: what this window would do, and the one button that does it (v0.21.1).
 *
 * Split out of `mwform.js` at the 400-line guard, on the seam that costs least: the other three
 * cards read and write a dozen pieces of draft state, while this one reads **the preview** and
 * **the visibility** and emits two events. A props boundary that narrow is a module boundary.
 *
 * ## What v0.21.0 got wrong here, and it was not cosmetic
 *
 * *"Schedule it"* did nothing. The form was sending `"targets": [null]` (F149), so both the live
 * preview and the create came back **422** — and this card answered that by printing
 * `[object Object]` at the foot of the form and leaving the button looking perfectly healthy. An
 * operator pressed it, nothing happened, and the screen said nothing about why.
 *
 * Three things follow from that, and they are the whole design of this card:
 *
 *  * **the refusal renders beside the button**, because that is where the operator is looking;
 *  * **a disabled button names what is missing**, because a dead control that does not say why is
 *    indistinguishable from a broken one — which is exactly the report this release answered;
 *  * **the button reports its own progress** (`Scheduling…`), so a slow appliance does not read
 *    as a dead one.
 */

import { html } from "../../dom.js";
import { plural } from "../../format.js";
import { session } from "../../session.js";
import { humanise } from "./mwdraft.js";

/* Why the submit is disabled, in the operator's own terms — or `""` when it is not.
 *
 * Pure and exported so `tests/test_maintenance_dom.py` can assert the sentence without a browser.
 * It names **one** step, the earliest unfinished one: telling somebody two things are wrong when
 * they can only fix the first is how a form teaches people to ignore it.
 */
export function blockedBecause(whatIsMissing, whenIsMissing) {
  if (whatIsMissing) return "Give the window a name and pick at least one host in step 1 first.";
  if (whenIsMissing) return "Set a start and an end in step 2 first.";
  return "";
}

export function ReviewCard(props) {
  const { preview: p, visibility, busy, error, ready, onVisibility } = props;
  const me = session();
  const blocker = blockedBecause(props.whatIsMissing, props.whenIsMissing);
  return html`<div>
    ${/* The dry run, and it is the API's own: the number here and the number an agent reads from
          `POST /preview` are the same number because they are the same call (Part III). The
          separators are elements — written as bare text between expressions, htm collapsed the
          newline and the line read `1 device ·1 active alarm`. */ null}
    <p class="mw-preview" data-role="preview">
      ${p
        ? html`<strong>${plural(p.devices, "device", "devices")}</strong
            ><span class="sep"> · </span
            ><strong>${plural(p.active_alarms, "active alarm", "active alarms")}</strong
            ><span class="sep"> · </span
            >${p.starts_in_s > 0 ? `starts in ${humanise(p.starts_in_s)}` : "starts now"}`
        : "Pick at least one host in step 1 to see what this would cover."}
    </p>
    ${p && p.targets_with_no_rule && p.targets_with_no_rule.length
      ? html`<p class="hint" data-role="silent-hosts">
          ${plural(p.targets_with_no_rule.length, "host", "hosts")} will send nothing at all.
        </p>`
      : null}
    ${p && p.needs_confirmation
      ? html`<p class="warn" data-role="needs-confirmation">
          Over six hours, so an editor or an admin has to confirm it before it takes effect.
        </p>`
      : null}
    <label
      >Who sees the details
      <select onChange=${(e) => onVisibility(e.target.value)}>
        <option value="editors" selected=${visibility === "editors"}>Editors and admins</option>
        <option value="everyone" selected=${visibility === "everyone"}>Everyone</option>
      </select>
    </label>
    <p class="hint">Everyone sees that the window exists, whichever you pick.</p>
    ${/* `me.user`, not `me.username` — the session carries the first and this line rendered
          `Owner:` followed by nothing for the whole of v0.21.0 (F149's field-name family). */ null}
    <p class="mw-owner muted">
      Owner <strong>${me ? me.user : "you"}</strong>${me ? ` · ${me.role}` : ""}
    </p>
    ${error ? html`<p class="error" role="alert" data-role="save-error">${error}</p>` : null}
    <div class="mw-submit">
      <button type="submit" class="primary" disabled=${busy || !ready} data-role="save">
        ${busy ? "Saving…" : props.editing ? "Save changes" : "Schedule it"}
      </button>
      ${blocker ? html`<span class="muted" data-role="save-blocked">${blocker}</span>` : null}
    </div>
  </div>`;
}
