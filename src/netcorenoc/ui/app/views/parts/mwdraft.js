/* The maintenance form's arithmetic, with no DOM in it (v0.21.0).
 *
 * Split from `mwform.js` on the seam `chartdata.js` already established one screen over: the
 * *values* a form computes are pure and are worth driving directly from tests, while the cards
 * that render them need a DOM. Everything here takes data and returns data.
 *
 * It is also where the one piece of genuinely dangerous arithmetic lives — turning a
 * `<input type="datetime-local">` value into an RFC 3339 instant **with an offset** — and that is
 * the reason this is a module and not four helpers at the bottom of the form: the API refuses a
 * naive datetime precisely so this conversion has to happen somewhere a reader can find.
 */

/* `/api/entities` rows as the form's own host shape. **The adapter F149 was missing.**
 *
 * The route serves a network element with its primary key as `id`. Everything downstream of this
 * form — `windowBody`, the rule chips, the target toggle — speaks `ne_id`, because that is what
 * the API's *request* body calls it. Those two names are both correct and they are not the same
 * name, and for one release nothing converted between them: the form read `host.ne_id` off a row
 * that has `id`, got `undefined` on every host, and sent `"targets": [null]`.
 *
 * Every request the form made came back **422** — the live preview, and the create behind
 * *"Schedule it"*. The screen looked like it worked and could not create a window at all.
 *
 * So the conversion is a named function with a test, rather than a property access repeated in
 * five places. A row with no usable id is **dropped**: a host that cannot be named in a request
 * cannot be a target, and offering it would put the operator back where they started.
 */
export function normaliseHosts(payload) {
  const rows = Array.isArray(payload) ? payload : payload && payload.entities ? payload.entities : [];
  return rows
    .map((row) => ({
      ne_id: row.ne_id === undefined ? row.id : row.ne_id,
      ip: row.ip || "",
      label: row.label || "",
    }))
    .filter((host) => Number.isInteger(host.ne_id));
}

/* The next quarter hour, as a `datetime-local` value.
 *
 * The commonest start anyone wants, and a default that is never in the past — which matters more
 * than it sounds: a window whose start has already gone by is `active` the moment it is created,
 * and an operator who meant *"in a few minutes"* would have suppressed their estate immediately.
 */
export function nextQuarter(now = new Date()) {
  const d = new Date(now.getTime());
  d.setSeconds(0, 0);
  d.setMinutes(Math.ceil((d.getMinutes() + 1) / 15) * 15);
  return localValue(d);
}

/* A `Date` as the exact string `<input type="datetime-local">` reads and writes. */
export function localValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/* `+2 h` on a `datetime-local` value, through `Date` so month ends and DST behave. */
export function plusHours(value, hours) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  d.setHours(d.getHours() + hours);
  return localValue(d);
}

/* `2026-05-14T10:00` + `UTC-03:00` -> `2026-05-14T10:00:00-03:00`.
 *
 * **The conversion the API's naive-datetime refusal exists to force.** A `datetime-local` input
 * yields a wall-clock string with no zone at all, and sending it would have the appliance guess —
 * which for an operator in Brasília scheduling a site in Riyadh is a seven-hour error, silently.
 *
 * The offset is the SITE's, taken from the server's own rendering of that zone, never the
 * browser's: the operator is typing the time they want *at the site*, and the browser's offset is
 * a fact about where they are sitting.
 */
export function withOffset(localInput, offsetLabel) {
  if (!localInput) return "";
  const offset = (offsetLabel || "UTC+00:00").replace("UTC", "") || "+00:00";
  const seconds = localInput.length === 16 ? `${localInput}:00` : localInput;
  return `${seconds}${offset}`;
}

/* `8100` -> `2 h 15 min`.
 *
 * D7 asks for the time until a window starts **as a number**, and this is the one place the
 * phrasing lives so the list, the form and the marker cannot word it three ways. Never negative:
 * a window that has started says `0 min`, and the caller decides whether to show it at all.
 */
export function humanise(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

/* The request body the form sends, from its state. Pure, so a test can assert the shape of what
 * would be sent without rendering anything.
 *
 * `all_day` rewrites the two instants to the day's ends rather than carrying a separate flag the
 * server would have to interpret: the appliance stores instants, and *"all day"* is a statement
 * about which instants, not a third kind of window.
 */
export function windowBody(state) {
  const start = state.allDay ? `${state.localStart.slice(0, 10)}T00:00` : state.localStart;
  const end = state.allDay ? `${state.localEnd.slice(0, 10)}T23:59` : state.localEnd;
  return {
    name: (state.name || "").trim(),
    description: state.description || "",
    organization_id: state.organizationId,
    tz: state.tz,
    starts_at: withOffset(start, state.siteOffset),
    ends_at: withOffset(end, state.siteOffset),
    all_day: Boolean(state.allDay),
    patch_s: (state.patchMinutes || 0) * 60,
    ledger_enabled: Boolean(state.ledgerEnabled),
    visibility: state.visibility,
    targets: (state.targets || []).map((t) => t.ne_id),
    rules: (state.rules || []).map((r) => ruleBody(r, state.siteOffset)),
  };
}

/* One chip as the API's rule shape. The `match_on` default is `varbind`, which is the narrower
 * reading of the maintainer's own example — his subtree is shaped like a table column carried in
 * varbinds rather than like a notification OID. */
export function ruleBody(rule, siteOffset) {
  if (rule.kind === "severity") {
    return { ne_id: rule.ne_id, kind: "severity", severity_rank: rule.severity_rank };
  }
  if (rule.kind === "oid") {
    return {
      ne_id: rule.ne_id,
      kind: "oid",
      oid_root: (rule.oid_root || "").trim(),
      match_on: rule.match_on || "varbind",
    };
  }
  return {
    ne_id: rule.ne_id,
    kind: "slot",
    slot_starts_at: withOffset(rule.slot_local_from, siteOffset),
    slot_ends_at: withOffset(rule.slot_local_to, siteOffset),
  };
}
