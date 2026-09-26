/* Formatting: time, counts, severity. Small, pure, and shared so two screens cannot disagree
 * about what "3 minutes ago" or "critical" looks like.
 *
 * ## Timestamps say which clock, and say it where an operator can see it (§IV.1, #294)
 *
 * During an incident the operator is reading a log in one window and this console in another, and
 * an unlabelled `14:32:07` is the single easiest way to make them disagree by an hour without
 * noticing.
 *
 * **That sentence has been in this header since v0.13.0 and was not true of the code.** Measured
 * in v0.16.4: nine surfaces printed a time and **none of them named a zone in visible text**;
 * `TIMEZONE` was referenced in exactly one place, an `overview.js` `title=`, so even there it was
 * hover-only — which on a phone means it does not exist.
 *
 * Every absolute time now carries its **offset from UTC in the text**: `2026-09-06 14:32:07 -03:00`.
 * The IANA zone name stays in the `title` and is stated once, visibly, in the shell. Relative
 * forms and the `.age` badges are zone-free by nature and are unchanged.
 *
 * ## Severity is encoded more than once (§IV.1)
 *
 * Colour alone fails for a colour-blind operator and on a bad monitor at 3 a.m. Every severity
 * carries a colour AND a level AND its word. `unplaced` is a first-class outcome, not a blank: a
 * blank cell would read as "no severity" rather than "not read yet".
 */

export const TIMEZONE = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "local time"; }
  catch { return "local time"; }
})();

/**
 * The offset from UTC of a given instant, as `+HH:MM` or `-HH:MM` (v0.16.4, DECISIONS #294).
 *
 * **Of the instant, not of now.** `getTimezoneOffset()` is a property of the `Date` it is called
 * on, so an alarm from before a daylight-saving change carries the offset that was in force when
 * it was raised. Reading it once at module load and reusing it would put the wrong offset on every
 * timestamp for half the year, and the operator correlating a 2 a.m. incident against a syslog is
 * exactly who would find it.
 *
 * `Z` for UTC, because `+00:00` is correct and `Z` is what a log file says.
 */
export function utcOffset(date) {
  const minutes = -date.getTimezoneOffset();
  if (minutes === 0) return "Z";
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  return `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

function pad(n) { return String(n).padStart(2, "0"); }

/**
 * An absolute timestamp: `YYYY-MM-DD HH:MM:SS ±HH:MM`, always, everywhere (DECISIONS #294).
 *
 * **Measured before this release: nine surfaces printed a time and none named a zone in visible
 * text.** `TIMEZONE` was referenced in exactly one place — an `overview.js` `title=` — so even
 * there it was hover-only, which on the phone half of this release means it did not exist. An
 * operator in Japan and one in Brazil read different numbers for the same event and no screen said
 * so.
 *
 * **Why a fixed shape rather than `toLocaleString()`.** The operator is reading this console in
 * one window and a syslog in another. `9/6/2026, 2:32:07 PM` and `06/09/2026 14:32:07` are the same
 * instant written two ways by two browsers, and neither says which zone. This is one string,
 * sortable, comparable by eye, and unambiguous with respect to UTC because the offset is *in* it.
 *
 * **Why the browser's zone rather than a chosen one.** A chosen zone is a per-user row, a migration
 * and a settings surface, for a value the browser already knows correctly. Naming the offset
 * removes the ambiguity the ask is actually about; choosing a different zone is a convenience on
 * top of it, and it is a ROADMAP line.
 *
 * Built from the `Date`'s own local getters rather than from `Intl`, so the output does not move
 * with the runner's locale — which is also what lets the DOM harness assert on it.
 */
export function absolute(epochSeconds) {
  if (epochSeconds == null) return "—";
  const d = new Date(epochSeconds * 1000);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${utcOffset(d)}`
  );
}

/** "4m ago", "2.1h ago". Compact, because it sits in a dense table beside the absolute form. */
export function relative(epochSeconds, now = Date.now() / 1000) {
  if (epochSeconds == null) return "—";
  const seconds = now - epochSeconds;
  const ago = seconds >= 0;
  const magnitude = Math.abs(seconds);
  const text =
    magnitude < 90 ? `${Math.round(magnitude)}s`
      : magnitude < 5400 ? `${Math.round(magnitude / 60)}m`
        : magnitude < 172800 ? `${(magnitude / 3600).toFixed(1)}h`
          : `${(magnitude / 86400).toFixed(1)}d`;
  return ago ? `${text} ago` : `in ${text}`;
}

/** The age of a live thing, unsuffixed, for a badge where "ago" is implied by context. */
export function age(epochSeconds, now = Date.now() / 1000) {
  const seconds = Math.max(0, now - epochSeconds);
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

/** Both forms plus the zone, for a `title=`. One string, so no screen invents its own. */
export function timeTitle(epochSeconds) {
  if (epochSeconds == null) return "no timestamp recorded";
  return `${absolute(epochSeconds)} (${TIMEZONE}) — ${relative(epochSeconds)}`;
}

/* ---------- what has been asserted about a grouping ---------- */

/**
 * The gesture kinds that assert something about a **grouping** (v0.16.4, DECISIONS #291).
 *
 * A **mirror** of `store/situation_events.py::ASSERTING_KINDS`, not a second opinion:
 * `tests/test_ui_invariants.py::test_the_console_and_the_store_agree_on_which_gestures_assert`
 * reads both files and fails if they diverge. The literal lives there because that is where the
 * prohibition is enforced — `PREREGISTRATION-0.16.0.md` §1 extends `incumbent_linked`'s rule to
 * any signal that is not an assertion about a grouping — and it is needed here because the card's
 * action surface turns on whether a judgement is already on record.
 *
 * `rename`, `manual_clear`, `self_clear`, `idle_close` and `operator_close` are deliberately
 * absent. Each of them **promotes** a situation to `open` and none of them says anything about
 * whether the alarms belong together, which is precisely why `open` is the wrong fact to key an
 * action surface on (v0.16.2's promotion/affirmation split).
 */
export const ASSERTING_KINDS = new Set(["verdict", "move", "merge", "operator_split"]);

/**
 * Has this situation already been judged, and by whom?
 *
 * Returns `null` when no asserting gesture is on record — the ordinary state of a `new` situation,
 * and of an `open` one that was promoted or renamed and nothing more. Otherwise the most recent
 * asserting event, so the card can say what was recorded rather than only that something was.
 */
export function lastJudgement(events) {
  let latest = null;
  for (const event of events ?? []) {
    if (!ASSERTING_KINDS.has(event.kind)) continue;
    if (latest === null || (event.at ?? 0) >= (latest.at ?? 0)) latest = event;
  }
  return latest;
}

/* ---------- severity ---------- */

/**
 * The X.733 scale, and the two states beside it (v0.22.0 item 3; v0.23.0; DECISIONS #276, #386, #391).
 *
 * `known_oids.SEVERITY_VOCAB` ranks `critical 0 … warning 3`, and `indeterminate`/`cleared` 4.
 * Ranks 0-3 are placements; rank 4 is the element saying *"I do not know"*, which is an answer and
 * gets its own band; `unplaced` is the appliance never having read one, which is a state and not an
 * error. No MEDIUM, because no token maps to one.
 *
 * **Every band is a level AND a colour AND a word** — `widgets.SeverityChip` draws all three. The
 * level is the ORDER, drawn as four bars filled from the left (critical 4, major 3, minor 2, warning
 * 1; indeterminate none filled; unplaced dashed): a magnitude reads as more-or-less at a glance and
 * needs no legend. v0.22.0's shapes (octagon, triangle, diamond, square) were a code to learn — a
 * triangle means "warning" in every other product — and v0.23.0 replaced them (#391).
 */
const SEVERITIES = [
  { rank: 0, key: "critical", level: 4, label: "critical" },
  { rank: 1, key: "major", level: 3, label: "major" },
  { rank: 2, key: "minor", level: 2, label: "minor" },
  { rank: 3, key: "warning", level: 1, label: "warning" },
];
export const INDETERMINATE = { rank: 4, key: "indeterminate", level: 0, label: "indeterminate" };
export const UNPLACED = { key: "unplaced", level: -1, label: "unplaced" };
const UNKNOWN = UNPLACED;

/** The placed bands, most severe first, then `indeterminate` — every band a count can land in. */
export const SCALE = [...SEVERITIES, INDETERMINATE];

/** `{ rank, key, level, label }` for a rank — **the only place a rank becomes a band**. */
export function band(rank) {
  return SCALE.find((entry) => entry.rank === rank) ?? UNKNOWN;
}

/**
 * The highest vocabulary rank, and therefore the top of the scale an integer field is mapped onto.
 * Derived from `SEVERITIES` rather than written as `3`, so a band added later moves the ceiling
 * with it — F92's lesson, in one constant.
 */
const MAX_RANK = Math.max(...SEVERITIES.map((entry) => entry.rank));

/**
 * The top of `known_oids.SEVERITY_VOCAB`: the line between the two scales. At or below it a rank
 * came from a bundled token; above it, only from a vendor's own integers, which mean nothing until
 * ordered against each other (F99). `tests/test_severity.py` pins it against `SEVERITY_VOCAB`.
 */
const VOCAB_MAX_RANK = 4;

/**
 * Place a vendor's integer rank on the bands **by order, not magnitude** (F99, v0.16.3).
 *
 * A vendor numbering severity 10/20/30 produces ranks the vocabulary does not hold. What
 * `confirm_ordinality` validated is their ORDER, so `{10, 20, 30}` and `{1, 5, 900}` both map to
 * `{0, 1, 2}`; with more values than bands the deepest share the last. The whole field is placed or
 * none of it is: `placementApplies` asks whether the SET leaves the vocabulary's range, so a lone
 * rank 4 stays `indeterminate`.
 */
function placeInteger(rank, ranks) {
  const scale = [...new Set([...(ranks ?? []), rank])].sort((a, b) => a - b);
  const index = scale.indexOf(rank);
  return Math.min(index, MAX_RANK);
}

function placementApplies(rank, ranks) {
  return Math.max(rank, ...(ranks ?? [])) > VOCAB_MAX_RANK;
}

/**
 * `{ key, level, label, known, text, declared }` for an alarm.
 *
 * **The declared wins and the learned is kept** (DECISIONS #284): precedence is decided at read
 * time, so the appliance's own judgement is never overwritten. `declared` says which value the chip
 * shows and `learned` carries the other, for its title. `known` is false when neither exists — the
 * `unplaced` band, never a default of "minor".
 */
export function severity(alarm) {
  const declared = alarm.declared_severity != null;
  // v0.23.0: a catalogue rule sits between the declaration and what was learned (ADR #385).
  const ruled = !declared && alarm.rule_severity != null;
  const value = declared ? alarm.declared_severity : ruled ? alarm.rule_severity : alarm.severity;
  if (value == null) return { ...UNKNOWN, known: false, text: "unplaced", declared: false };
  const raw = declared ? alarm.declared_severity_rank
    : ruled ? alarm.rule_severity_rank : alarm.severity_rank;
  // A declared severity is a vocabulary token by construction — the route refuses anything else —
  // so only a learned `int`-kind rank can land outside the bands.
  const rank =
    !declared && !ruled && typeof raw === "number" && placementApplies(raw, alarm.severity_ranks)
      ? placeInteger(raw, alarm.severity_ranks)
      : raw;
  return {
    ...band(rank),
    known: true,
    text: String(value),
    rank,
    declared,
    ruled,
    learned: alarm.severity ?? null,
  };
}

/* ---------- numbers ---------- */

/** Group thousands so a five-digit row count is readable in a dense table. */
export function count(value) {
  if (value == null) return "—";
  return Number(value).toLocaleString("en-US");
}

/** A fixed-width fraction, for a column of scores that must align on the decimal point. */
export function score(value, digits = 2) {
  return value == null ? "—" : Number(value).toFixed(digits);
}

export function percent(fraction, digits = 0) {
  return fraction == null ? "—" : `${(Number(fraction) * 100).toFixed(digits)}%`;
}

/** Pluralise without a library: `plural(1, "alarm")` -> "1 alarm". */
export function plural(n, singular, pluralForm) {
  return `${count(n)} ${n === 1 ? singular : pluralForm ?? `${singular}s`}`;
}

/** Bytes-free size for a row budget: "12.4k rows" reads faster than "12 400 rows" in a chip. */
export function compact(value) {
  if (value == null) return "—";
  const n = Number(value);
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** The name an operator gave a thing, falling back to what the network calls it. */
export function alarmName(alarm) {
  return alarm.class_label || alarm.class_name || alarm.class_oid;
}
export function deviceName(alarm) {
  return alarm.device_label || alarm.device_ip;
}

/**
 * The vendor, **only when the name above fell through to the raw OID** (v0.16.3, DECISIONS #282).
 *
 * A vendor is not a name, so it never enters `alarmName`'s chain: appending it there would put
 * `Huawei` in the slot the whole chain reserves for what a trap *means*, and an operator reading a
 * name column would be told a manufacturer instead. Beside the OID it says exactly what is true —
 * *this is an unnamed Huawei trap, here is its OID*.
 *
 * Measured on a ten-scenario corpus: 48 classes, **2 with a standard-trap name, 46 with a vendor,
 * 0 declared.** So this reaches 46 of the 46 rows that would otherwise read as a bare OID, and it
 * is not a substitute for the 46 names that are still missing until an operator writes one.
 */
export function classVendor(alarm) {
  if (alarm.class_label || alarm.class_name) return null;
  return alarm.class_vendor || null;
}
