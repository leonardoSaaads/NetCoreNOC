/* Formatting: time, counts, severity. Small, pure, and shared so two screens cannot disagree
 * about what "3 minutes ago" or "critical" looks like.
 *
 * ## Timestamps say which clock (§IV.1)
 *
 * Every absolute time this console prints is accompanied by its relative form and by the
 * timezone it is in. During an incident the operator is reading a log in one window and this
 * console in another, and an unlabelled `14:32:07` is the single easiest way to make them
 * disagree by an hour without noticing.
 *
 * ## Severity is encoded more than once (§IV.1)
 *
 * Colour alone fails for a colour-blind operator and on a bad monitor at 3 a.m. Every severity
 * carries a colour AND a glyph AND its text, and the rank when one is known. `unknown` is a
 * first-class outcome, not a blank: this product learns severity and has not always learned it,
 * and a blank cell would read as "no severity" rather than "not learned yet".
 */

export const TIMEZONE = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "local time"; }
  catch { return "local time"; }
})();

/** Absolute, with the timezone named. Seconds included: incidents are read at second precision. */
export function absolute(epochSeconds) {
  if (epochSeconds == null) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
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
 * The four bands the appliance can place an element on, and one it cannot (DECISIONS #276).
 *
 * **Five vocabulary ranks, five bands, and no MEDIUM.** `known_oids.SEVERITY_VOCAB` normalises
 * `critical=0, major=1, minor=2, warning=3, indeterminate=4, cleared=4`. Ranks 0-3 are placements
 * on a scale and get a band each; **rank 4 is not a placement** — `indeterminate` is the
 * vocabulary's own word for *"I do not know how serious this is"* — so it renders as UNKNOWN
 * carrying that word, beside a never-learned severity and visibly distinct from it by its text.
 *
 * Until v0.16.2 ranks 3 and 4 were folded into `low`, which said *"not very serious"* about an
 * element that had said nothing of the kind. That is the defect this module's own header already
 * names for the blank cell — *"a blank would read as 'no severity' rather than 'not learned
 * yet'"* — and the same argument condemns the fold.
 *
 * **A MEDIUM is not invented.** No token in the bundled vocabulary maps to one, and the two ways
 * to produce a band are to rename `minor` — a word no NE emits and no MIB carries — or to add a
 * band nothing can reach. A badge that can show a level the appliance cannot produce is worse
 * than one that shows four.
 *
 * **Each glyph is a distinct SHAPE.** `critical` and `major` both drew `▲` until this release, and
 * they are the pair that also collides on hue under deuteranopia: two adjacent bands sharing a
 * glyph and two hue-steps of red-orange are one encoding, not the three the rule requires.
 */
const SEVERITIES = [
  { rank: 0, key: "crit", glyph: "▲", label: "critical" },
  { rank: 1, key: "major", glyph: "◆", label: "major" },
  { rank: 2, key: "minor", glyph: "●", label: "minor" },
  { rank: 3, key: "low", glyph: "▬", label: "low" },
];
const UNKNOWN = { key: "unknown", glyph: "?", label: "unknown" };

/**
 * The highest vocabulary rank, and therefore the top of the scale an integer field is mapped onto.
 * Derived from `SEVERITIES` rather than written as `3`, so a band added later moves the ceiling
 * with it — F92's lesson, in one constant.
 */
const MAX_RANK = Math.max(...SEVERITIES.map((entry) => entry.rank));

/**
 * The top of `known_oids.SEVERITY_VOCAB`, which is one step **above** the last rendered band:
 * `indeterminate` and `cleared` both rank 4, and rank 4 is deliberately not a placement — it is
 * the vocabulary's own word for *"I do not know how serious this is"*, so it renders as UNKNOWN.
 *
 * It is the line between the two scales. A rank at or below it came from a bundled token and
 * means what that token means; a rank above it can only have come from `_candidate_ranks`'
 * `int` kind, where the number is a vendor's own and means nothing until it is ordered against
 * the others (F99). `tests/test_severity.py` pins this constant against `SEVERITY_VOCAB` itself,
 * so the two languages cannot drift apart silently.
 */
const VOCAB_MAX_RANK = 4;

/**
 * Place an arbitrary integer rank on the rendered bands (**F99's repair**, v0.16.3).
 *
 * `severity.py::_candidate_ranks` returns `kind="int"` with the varbind's **raw integer** as the
 * rank whenever the observed values are not bundled tokens, bounded only by
 * `SEVERITY_MAX_DISTINCT = 8` distinct values and not at all in magnitude. A vendor that numbers
 * severity 10, 20, 30 produced ranks 10, 20 and 30 — and every one of them fell off the end of
 * `SEVERITIES` and rendered as the same `unknown` pill. **Three severities the appliance had
 * validated against observed lifetimes, collapsed into one band at the last step.**
 *
 * *(F99 as issued says they render as `low`. That was true until v0.16.2's pill moved out-of-band
 * ranks to UNKNOWN in the same release the finding was written in; the entry is corrected there.
 * The defect survives either way — the ordering the appliance proved is discarded.)*
 *
 * **Rank-order, not linear.** `{10, 20, 30}` maps to `{0, 1, 2}` and so does `{1, 5, 900}`: what
 * `confirm_ordinality` validated is the **order** of the values, never the distance between them,
 * so a linear scaling would render a spacing the evidence never supported. The ranks present are
 * sorted and spread across the bands most-severe first, which is the direction the vocabulary
 * uses. With more distinct values than bands the deepest ones share the last band — at most 8
 * values against 4 bands, so at most five share one.
 *
 * `ranks` is the field's whole observed set, which the caller has because every alarm row carries
 * its own; with only one rank in hand the mapping is *this value is the most severe I have seen*,
 * which is the honest reading of a single observation.
 *
 * **The whole field is placed, or none of it is** — `applies` asks whether the *set* leaves the
 * vocabulary's range, not whether one value does. A field reading `{1, 4, 7}` is a vendor's own
 * numbering and all three are placed by order; a lone rank 4 is `indeterminate` and is left where
 * the vocabulary put it. Deciding value by value would render two members of one scale on two
 * different scales.
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
 * `{ key, glyph, label, known, text, declared }` for an alarm.
 *
 * **The declared wins and the learned is kept** (v0.16.3, DECISIONS #284). Precedence is decided
 * here, at read time, because the appliance's own judgement is never overwritten: a declaration
 * and 200 observations disagreeing is evidence, and an overwrite spends it. `declared` says which
 * value the pill is showing so the screen can mark it, and `learned` carries the other one so the
 * pill's `title` can name it without a second request.
 *
 * `known` is `false` when neither exists — a true statement about a zero-config product on its
 * first day, displayed as such rather than as an empty cell or a default of "minor".
 *
 * Rank 4 still renders as UNKNOWN carrying its own word: `indeterminate` is the vocabulary's term
 * for *"I do not know how serious this is"*, which is a placement on no scale. An integer rank
 * outside 0-`MAX_RANK` is placed by `placeInteger` rather than discarded (F99).
 */
export function severity(alarm) {
  const declared = alarm.declared_severity != null;
  const value = declared ? alarm.declared_severity : alarm.severity;
  if (value == null) return { ...UNKNOWN, known: false, text: "unknown", declared: false };
  const raw = declared ? alarm.declared_severity_rank : alarm.severity_rank;
  // A declared severity is a vocabulary token by construction — the route refuses anything else —
  // so only a learned `int`-kind rank can land outside the bands.
  const rank =
    !declared && typeof raw === "number" && placementApplies(raw, alarm.severity_ranks)
      ? placeInteger(raw, alarm.severity_ranks)
      : raw;
  const found = SEVERITIES.find((entry) => entry.rank === rank) ?? UNKNOWN;
  return {
    ...found,
    known: true,
    text: String(value),
    rank,
    declared,
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
