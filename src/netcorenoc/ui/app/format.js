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

/* ---------- severity ---------- */

const SEVERITIES = [
  { rank: 0, key: "crit", glyph: "▲", label: "critical" },
  { rank: 1, key: "major", glyph: "▲", label: "major" },
  { rank: 2, key: "minor", glyph: "◆", label: "minor" },
];
const LOW = { key: "low", glyph: "▬", label: "low" };
const UNKNOWN = { key: "unknown", glyph: "?", label: "unknown" };

/**
 * `{ key, glyph, label, known }` for an alarm.
 *
 * `known` is `false` when the appliance has not learned a severity for this element — which is a
 * true statement about a zero-config product on its first day, and is displayed as such rather
 * than as an empty cell or a default of "minor".
 */
export function severity(alarm) {
  if (alarm.severity == null) return { ...UNKNOWN, known: false, text: "unknown" };
  const rank = alarm.severity_rank;
  const found = SEVERITIES.find((entry) => entry.rank === rank) ?? LOW;
  return { ...found, known: true, text: String(alarm.severity), rank };
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
