/* The Overview's appliance half: is it keeping up, and what has it learned (v0.16.7, #318).
 *
 * Split out of `views/parts/pulse.js`, which reached **17 095 bytes against a 17 579-byte
 * ceiling** — 484 bytes of headroom, and this release adds a band. The seam is not the size: it is
 * that the file held two subjects. Everything above this line in the old file is about **the
 * network** — how bad it is, what is happening to it, where, and which element is worst. Everything
 * here is about **the appliance itself**, which is the same line #300 drew when it moved the four
 * correlation counters under one word in the health panel, and the line the maintainer's own
 * reading order draws between bands 3 and 4.
 *
 * The handoff proposed splitting by chart type — the series against the estate. That seam was
 * refused after measurement: it would have put the queue-depth series beside the situations series
 * and the estate map beside the receiver counters, grouping files by which primitive they call
 * rather than by what an operator is asking. `charts.js` already owns the primitives.
 */

import { html } from "../../dom.js";
import { Stat, SectionHeading } from "../../widgets.js";
import { Series } from "../../charts.js";
import { buckets, clock, spanText } from "../../chartdata.js";
import { plural, count, score } from "../../format.js";

/**
 * **Band 4 — is the appliance keeping up.**
 *
 * The host series are the same three `/api/stats.resources` carries for the top bar's control, at a
 * size that can hold an axis: the control's sparkline is 104 x 22 px and carries none. One
 * vocabulary, two sizes, and never two implementations — both go through `charts.js`.
 *
 * **The restart sentence is not decoration.** `resources.py` keeps a `deque` in process memory and
 * nothing writes it to the database: no migration creates a table whose name holds `sample`,
 * `series`, `history`, `metric` or `snapshot`, measured. So the two-hour window is two hours *since
 * this process started*, and a screen that implied otherwise would be claiming a history the
 * appliance does not have.
 */
export function Keeping({ stats, ring, rate }) {
  const res = stats.resources;
  const receiver = stats.receiver;
  const ringGrid = buckets((ring && ring.at) || [], 60);
  /* **The host series get a real time axis, and that is a repair the live pass forced.**
   *
   * They had none: three `line` charts with no `labels`, and a caption reading *"last 2 hours"*
   * over a series that, on an appliance up for a minute, held two points. The window was a claim
   * about the ring rather than a statement about the data — which is precisely what DECISIONS #306
   * forbids, reached from the one direction that decision did not look, because `resources` serves
   * values with **no timestamps at all**.
   *
   * `bucket_s` (v0.16.6, additive) is how much time one served point covers, so the span is
   * `points x bucket_s` — derived from the array, like every other axis here — and the labels are
   * clock times counted back from now. `window_s` is still named, as the **upper bound** it is. */
  const hostAxis = (values) => {
    const points = (values || []).length;
    const bucket = Number(res && res.bucket_s) || 0;
    if (!points || !bucket) return { labels: [], span: null };
    const newest = Date.now() / 1000;
    const stamps = Array.from({ length: points }, (_, i) => newest - (points - 1 - i) * bucket);
    /* **`clock` per point, and NOT `buckets(...).labels`** — a second thing the live pass caught.
     *
     * `buckets` labels each bucket's START, which is right for a column chart where a bar covers
     * `[start, start + width)`. Here each value is a bucket MEAN, so a point is an instant, and
     * routing through `buckets` put the last tick at `newest - width`: two readings five minutes
     * apart were labelled two and a half minutes apart. A tick one bucket short renders perfectly
     * and is wrong, which is this release's whole difficulty in one line. */
    const span = points > 1 ? stamps[points - 1] - stamps[0] : 0;
    return {
      labels: stamps.map((ts) => clock(ts, span)),
      // The coverage, and the ring's window as the upper bound it is. Each point is a mean over
      // `bucket_s`, so `points x bucket_s` is what the series covers — never `window_s`, which is
      // what the ring COULD hold and what the caption used to claim.
      span: `${spanText(points * bucket)} of a ${spanText(res.window_s)} window`,
    };
  };
  const verdict = keepingUp(res, stats, receiver);
  return html`<section class="panel-block">
    <${SectionHeading} title="Is the appliance keeping up" />
    ${/* **The heading asks a question, so something has to answer it.** Four charts and a run of
          counters are the evidence for an answer, not the answer, and an operator who has to
          read four axes to find out whether anything is wrong will stop opening the panel. Same
          shape as the grouping verdict on Situations: one line, coloured, and the charts below
          it are what you read when the line says to. */ null}
    <p class="verdict verdict-${verdict.tone}">${verdict.text}</p>
    ${/* **The queue chart is OUTSIDE the `resources` branch, and that placement is a repair.**
          It was inside it first, and a DOM test drove an `/api/stats` with no `resources` block
          and found the chart gone. `queue_depth` is served whether or not the process runner is
          present and the series is derived in this browser, so gating it on the host block would
          lose the one chart that says whether correlation is falling behind — for a reason that
          has nothing to do with it. Two sources, two conditions. */ null}
    <div class="chart-grid">
      ${/* **The captions name what is being measured, not which endpoint served it.**
            They read `/api/stats.resources — the host's memory` before, under four charts on the
            screen an operator opens first. The endpoint is a fact about this console's plumbing
            and the operator is not debugging the console; what they need is which memory, whose
            filesystem, how often. The path stays where a reader who wants it will look for it —
            in the module that fetches it — rather than on the face of the chart. */ null}
      ${res
        ? [
            ["CPU", res.cpu_series, "%",
             res.cpu_count ? `${res.cpu_count} cores` : "this host", null],
            ["Memory", res.mem_series, "%",
             res.mem_source === "cgroup" ? "this container" : "this host", null],
            ["Storage", res.disk_series, "%", "the database's filesystem", null],
            /* **The database itself, which nothing measured until v0.19.0.** "Storage" above is
               the filesystem, and an operator reading 89 % there cannot tell whether this
               appliance is responsible for it. A size in megabytes over the same window answers
               both *how big has my correlator got* and *how fast is it growing*, which is the
               question retention settings are the answer to. In MB, not per cent: a database has
               no ceiling to be a share of. */
            ["Database", res.db_series, "MB", "the file and its journal", null],
          ].map(([label, values, unit, what, note]) => {
            const axis = hostAxis(values);
            return html`<${Series} key=${label} title=${label} unit=${unit}
              series=${[{ name: label, values }]} labels=${axis.labels}
              source=${what} span=${axis.span} note=${note} />`;
          })
        : null}
      <${Series} title="Queue depth" unit="traps"
        series=${[{ name: "queued", tone: "warn", values: (ring && ring.queue) || [] }]}
        labels=${ringGrid.labels}
        source="waiting to be correlated"
        span=${ringGrid.n > 1 ? `over ${spanText(ringGrid.spanS)}` : null}
        note="this browser only" />
    </div>
    ${res
      ? null
      : html`<p class="hint">No <code>resources</code> block in <code>/api/stats</code>: this API
          runs without the process runner, so nothing samples the host. Queue depth is
          unaffected.</p>`}
    ${/* **The counters F68 found, as one line rather than seven tiles.**
          They did not leave: `receiver.denied` is still the only evidence an operator has that
          their own allowlist is refusing their own equipment. What left is the tile chrome — two
          headings, two paragraphs and seven boxes — for numbers that are read when the answer
          above is "no" and skipped when it is "yes" (#300's shape for the health panel).

          Each carries `data-metric`, which is the same `label`/`value` pairing `.stat` has had
          since v0.13.0 and is what lets the DOM harness read a number by its name instead of by
          its position in a sentence. */ null}
    ${/* The separator is **an element**, not a CSS `::before`, and that is F112's second half.
          `content: " · "` renders a gap and contributes nothing to `textContent`, so the line read
          `0.0000 strap rate` and `0accepted 0refused` to a screen reader and to the clipboard
          while looking correct on screen. `charts.js` already had this right — `.chart-sep` is a
          real span — and this is the same answer in the same words. */ null}
    <p class="health-sub">
      ${[
        { name: "p95 latency", value: `${score(stats.latency_p95_s ?? 0, 4)} s` },
        {
          name: "trap rate",
          value: rate ? `${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s` : "—",
          note: rate ? `over ${rate.windowS.toFixed(1)} s` : "waiting for a second reading",
        },
        // **Only what is non-zero, plus the two always worth a glance** (v0.20.0). Five counters
        // reading `refused 0 · quarantined 0 · dropped 0` is three facts an operator reads to
        // learn nothing. A zero here is the good case and the good case needs no words; a
        // non-zero one is the whole reason the line exists.
        ...(receiver
          ? ["received", "accepted", "denied", "quarantined", "dropped"]
              .filter((key) => key === "received" || key === "accepted" || receiver[key])
              .map((key) => ({
                name: key === "denied" ? "refused" : key,
                metric: key,
                value: count(receiver[key]),
              }))
          : []),
      ].map((one, index) => html`<span key=${one.metric || one.name}>
        ${index ? html`<span class="metric-sep"> · </span>` : null}
        <${Metric} ...${one} />
      </span>`)}
    </p>
    ${receiver && receiver.denied
      ? html`<p class="warnbox">The trap allowlist has refused${" "}
          <b>${plural(receiver.denied, "datagram")}</b>. A refused source is dropped at the socket:
          it is not quarantined and it never becomes an alarm.</p>`
      : null}
  </section>`;
}

/**
 * The one-line answer to the panel's own question, and the rule it applied.
 *
 * **Every branch is a threshold on a number this panel already draws**, so the line can never
 * disagree with the charts under it. The order is the order an operator would act in: something
 * that stops the appliance outright (a full filesystem), then something that loses traps (a
 * growing queue, a refusing allowlist), then load, then the all-clear. `null` readings are not
 * failures and never a zero — a metric this host does not expose simply does not vote.
 */
export function keepingUp(res, stats, receiver) {
  const disk = res && res.disk_pct;
  const cpu = res && res.cpu_pct;
  const mem = res && res.mem_pct;
  const dropped = (receiver && receiver.dropped) || 0;
  const denied = (receiver && receiver.denied) || 0;
  if (disk != null && disk >= 90) {
    return { tone: "bad", text: `Storage is ${disk}% full — this appliance stops when it fills.` };
  }
  if (dropped > 0) {
    return { tone: "bad", text: `${count(dropped)} traps were dropped — it is not keeping up.` };
  }
  if (denied > 0) {
    return { tone: "warn", text: `${count(denied)} datagrams refused by the trap allowlist.` };
  }
  if ((cpu != null && cpu >= 85) || (mem != null && mem >= 85)) {
    return { tone: "warn", text: `Running hot — CPU ${cpu ?? "—"}%, memory ${mem ?? "—"}%.` };
  }
  if (cpu == null && mem == null && disk == null) {
    return { tone: "quiet", text: "This host exposes no CPU, memory or storage readings." };
  }
  /* **"Keeping up" with nothing to keep up with is not an answer**, and the first cut said
     exactly that: a freshly restarted appliance read `Keeping up — 0 traps accepted`. Nothing had
     arrived, so the appliance had not been tested and the panel had no business grading it. */
  const accepted = (receiver && receiver.accepted) || 0;
  if (!accepted) {
    return { tone: "quiet", text: "No traps have arrived yet, so there is nothing to keep up with." };
  }
  return {
    tone: "ok",
    text: `Keeping up — ${count(accepted)} traps accepted, none dropped, CPU ${cpu ?? "—"}%.`,
  };
}

/**
 * One number on the secondary line: its name, its value, and optionally what it is over.
 *
 * `data-metric` names the number rather than leaving it to be found by position in a sentence —
 * the same reason `.stat` carries `.stat-label` and `.stat-value`. A guard that read
 * `"received"` out of a run of prose would break the day a separator changed, which is a guard
 * measuring the copy instead of the fact.
 */
function Metric({ name, metric, value, note }) {
  /* **`${" "}` and not a CSS margin, and the difference is F112.**
   *
   * Written first as `<span>${name}</span>\n<b>${value}</b>`, which rendered `p95 latency0.0000 s`
   * — `htm` drops a whitespace-only run between two elements. F110's guard exempts
   * element-to-element pairs on the stated ground that *"the container is a flex row whose `gap`
   * separates them"*, and `.health-sub` is a `<p>`: `display: block`, no gap, premise false.
   *
   * The first repair was `margin-right` on the name, and **it was wrong in a way worth recording**:
   * a margin fixes the pixels and leaves `textContent` as `p95 latency0.0000 s`, which is what a
   * screen reader announces and what an operator pastes into a ticket. An explicit space is in the
   * DOM, so all three agree.
   */
  return html`<span class="metric" data-metric=${metric || name}>
    <span class="metric-name">${name}</span>${" "}<b>${value}</b>
    ${note ? html`${" "}<span class="metric-note">${note}</span>` : null}
  </span>`;
}

/** **Band 5 — what the appliance has learned.** Three numbers, and they are numbers, not prose. */
export function Learned({ stats }) {
  return html`<div class="stat-row">
    <${Stat} label="devices" value=${stats.devices} note="learned, not configured" />
    <${Stat} label="alarm classes" value=${stats.classes} note="learned, not configured" />
    ${(stats.ingest_gaps || []).length
      ? html`<${Stat} label="ingest gaps" value=${stats.ingest_gaps.length} tone="warn"
                      note="closed gaps, kept for history" />`
      : null}
  </div>`;
}
