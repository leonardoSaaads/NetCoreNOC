/* Card 2: when (v0.29.0).
 *
 * Split out of `mwform.js` when the card grew the controls an operator reaches for first:
 *
 *  * **the site's zone as a value, not a placeholder** — it reads as set, and a search changes it;
 *  * **start presets** (now, in an hour, tonight) and **duration chips** (30 min to 8 h), because
 *    typing two `datetime-local` values is the slow path to the commonest windows;
 *  * **changing the start keeps the duration** — the old card reset the end to start + 2 h, so
 *    moving a four-hour window by an hour silently made it a two-hour one;
 *  * the duration, the site and your own clock in **one line**, and the D6 consequence of a window
 *    over six hours said here, where the length is chosen, not first in the last card.
 *
 * Every value stays a wall clock **at the site**; `mwdraft.withOffset` adds the site's offset.
 */

import { html } from "../../dom.js";
import { SiteAndYourTime, TimelineBar, ZonePicker } from "./mwtime.js";
import { humanise, localValue, siteLocal, withOffset } from "./mwdraft.js";

/* The durations an operator picks most, as chips. Anything else is the End field. */
export const DURATIONS = [[30, "30 min"], [60, "1 h"], [120, "2 h"], [240, "4 h"], [480, "8 h"]];

const SIX_HOURS_S = 6 * 3600;

/* `value` + `minutes`, on a `datetime-local` string, through `Date` so month ends behave. */
export function plusMinutes(value, minutes) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  d.setMinutes(d.getMinutes() + minutes);
  return localValue(d);
}

/* Whole minutes from one wall clock to another at the same site; NaN when either is unset. */
export function spanMinutes(start, end) {
  const a = Date.parse(start);
  const b = Date.parse(end);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 60000) : NaN;
}

/* The start presets, as wall clocks AT THE SITE: now, an hour from now, and 22:00 tonight —
 * tomorrow's once tonight's has passed. Pure in `nowMs`, so the DOM harness can pin it. */
export function startPresets(tz, nowMs = Date.now()) {
  const here = siteLocal(Math.floor(nowMs / 60000) * 60, tz);
  const tonight = `${here.slice(0, 10)}T22:00`;
  const late = here >= tonight;
  return [
    ["Now", here],
    ["In 1 h", plusMinutes(here, 60)],
    [late ? "Tomorrow 22:00" : "Tonight 22:00", late ? plusMinutes(tonight, 24 * 60) : tonight],
  ];
}

export function WhenCard({ s, onChange, onZone, canConfirm }) {
  const startAt = Date.parse(withOffset(s.localStart, s.siteOffset)) / 1000;
  const endAt = Date.parse(withOffset(s.localEnd, s.siteOffset)) / 1000;
  const bad = !(endAt > startAt);
  const minutes = spanMinutes(s.localStart, s.localEnd);
  const keep = minutes > 0 ? minutes : 120;
  const setStart = (value) => onChange({ localStart: value, localEnd: plusMinutes(value, keep) });
  return html`<div class="mw-whencard">
    <${ZonePicker} value=${s.tz} offset=${s.siteOffset} onPick=${onZone} />
    <label class="mw-allday"
      ><input type="checkbox" checked=${s.allDay}
        onChange=${(e) => onChange({ allDay: e.target.checked })} />
      All day</label>
    ${s.allDay
      ? html`<label>Day
          <input type="date" value=${s.localStart.slice(0, 10)}
            onInput=${(e) => onChange({
              localStart: `${e.target.value}T00:00`, localEnd: `${e.target.value}T23:59`,
            })} /></label>`
      : html`<div class="mw-when">
          <div class="mw-field">
            <label for="mw-start">Start</label>
            <input id="mw-start" type="datetime-local" value=${s.localStart}
              onInput=${(e) => setStart(e.target.value)} />
            <div class="mw-presets" role="group" aria-label="Start">
              ${startPresets(s.tz).map(([label, value]) => html`<button type="button" key=${label}
                aria-pressed=${value === s.localStart ? "true" : "false"}
                onClick=${() => setStart(value)}>${label}</button>`)}
            </div>
          </div>
          <div class="mw-field">
            <label for="mw-end">End</label>
            <input id="mw-end" type="datetime-local" value=${s.localEnd}
              aria-invalid=${bad ? "true" : "false"}
              onInput=${(e) => onChange({ localEnd: e.target.value })} />
            <div class="mw-presets" role="group" aria-label="Duration" data-role="durations">
              ${DURATIONS.map(([m, label]) => html`<button type="button" key=${label}
                aria-pressed=${m === minutes ? "true" : "false"}
                onClick=${() => onChange({ localEnd: plusMinutes(s.localStart, m) })}>${label}</button>`)}
            </div>
          </div>
        </div>`}
    ${bad
      ? html`<p class="error" data-role="when-error">The end has to be after the start.</p>`
      : html`<p class="mw-span" data-role="span"><strong>${humanise(endAt - startAt)}</strong>
          <span class="sep"> · starts </span><${SiteAndYourTime} compact=${true} instant=${startAt}
            siteZone=${s.tz} siteTime=${withOffset(s.localStart, s.siteOffset)}
            siteOffset=${s.siteOffset} /></p>`}
    ${!bad && endAt - startAt > SIX_HOURS_S
      ? html`<p class="warn" data-role="when-long">Over 6 h: ${canConfirm
          ? "you confirm it when you schedule it."
          : "an editor or an admin has to confirm it before it starts."}</p>`
      : null}
    <div class="mw-patch">
      <label for="mw-patch">Patch band</label>
      <input id="mw-patch" type="number" min="0" max="120" step="1" value=${s.patchMinutes}
        onInput=${(e) => onChange({ patchMinutes: Math.max(0, Number(e.target.value) || 0) })} />
      <span class="muted">min either side</span>
    </div>
    <${TimelineBar} startsAt=${startAt} endsAt=${endAt} patchS=${s.patchMinutes * 60}
      now=${Date.now() / 1000} />
  </div>`;
}
