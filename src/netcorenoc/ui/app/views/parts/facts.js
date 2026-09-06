/* The two read-only parameter classes: **hardening-only** and **structural** (draft §6).
 *
 * Split out of `settings.js` because these render facts rather than forms, and the distinction is
 * the point of the screen. §6.1:
 *
 * > A structural value is rendered *visibly differently* from a settable one — not a disabled
 * > input. A greyed-out field says "you may not"; a fact says "this is what is true". They are
 * > different sentences and the UI must not conflate them.
 *
 * So nothing here renders an `<input>` at all. An admin who sees *"seal: intact, 0 queries"* and
 * finds no control learns something true: it is a guarantee, not a preference.
 */

import { html } from "../../dom.js";
import { DataTable, SectionHeading, Empty } from "../../widgets.js";
import { plural, score } from "../../format.js";
import { HARDENING, STRUCTURAL, RESTART_REQUIRED } from "../../parameters.js";

/** Hardening-only: the floors, where they are enforced, and which of them have a write path. */
export function Hardening({ scorer }) {
  return html`<section class=${`panel-block param-${HARDENING}`}>
    <${SectionHeading} title="Hardening-only"
      hint=${"resolved = max(project floor, deployment policy) — stricter only. A looser value " +
             "is refused here before it is sent, and by the appliance if it is sent anyway."} />
    ${scorer ? html`<${DataTable}
      caption="The scorer's degeneracy bounds — enforced live, on the Link scorer screen"
      columns=${[
        { key: "name", label: "floor" },
        { key: "value", label: "project value", numeric: true },
        { key: "direction", label: "you may" },
        { key: "where", label: "where it is enforced" },
      ]}
      rows=${[
        ["minimum tau", scorer.bounds.min_tau_s, "raise it", "scoring.validate_params, and this console"],
        ["maximum tau", scorer.bounds.max_tau_s, "lower it", "scoring.validate_params, and this console"],
        ["minimum weight sum", scorer.bounds.min_weight_sum, "raise it", "scoring.validate_params, and this console"],
        ["threshold margin", scorer.bounds.threshold_margin, "raise it", "scoring.validate_params, and this console"],
      ].map(([name, value, direction, where]) => ({
        key: name,
        cells: { name, value: score(value, 4), direction, where },
      }))} />`
      : html`<p class="hint">The scorer's bounds could not be read, so they are not shown rather
          than guessed.</p>`}

    <p class="structural-note"><b>The sufficiency floors have no write path.</b>${" "}
      <code>asserting_bags ≥ 50</code> and <code>asserting_incidents ≥ 30</code> are constants in
      the promotion route: changing them changes the gate's inputs. See${" "}
      <code class="mono">docs/ROADMAP.md</code>.</p>
  </section>`;
}

/** Structural: facts, with no control, because there is no choice. */
export function Structural({ config, scorer }) {
  const rows = [
    ["sealed holdout", "displayed on the Judge & promotion screen",
     "The holdout's query count is a property of what has been read, not a setting. A control " +
     "that could change it would make the number meaningless."],
    ["audit hash chain", "verified by `python -m netcorenoc audit verify`",
     "Each row hashes the row before it. There is no repair, because a repairable chain is not " +
     "evidence."],
    ["asserting_bags floor", "50",
     "Pre-registered in PREREGISTRATION-0.10.0 §2.2 before any data was seen. Registering a " +
     "floor after seeing the data is choosing the answer."],
    ["asserting_incidents floor", "30", "Pre-registered alongside the bag floor."],
    ["eval output hash", "byte-identical across releases",
     "The offline evaluation gate reproduces byte-for-byte. That is a property of the pipeline " +
     "and not something a deployment tunes."],
    ["capability ceiling", "rbac.PERMISSIONS",
     "A stored policy can only take capabilities away. The compiled map is the maximum any role " +
     "may ever hold, and an intersection cannot exceed its first operand."],
    ["scorer contract version", scorer ? String(scorer.contract_version) : "—",
     "Which scoring contract the running engine implements."],
  ];
  return html`<section class=${`panel-block param-${STRUCTURAL}`}>
    <${SectionHeading} title="Structural — facts, not settings"
      hint="No edit control, because these are not choices. A greyed-out field would say “you may
            not”; these say “this is what is true”." />
    <dl class="facts">
      ${rows.map(([name, value, why]) => html`<div class="fact" key=${name}>
        <dt>${name}</dt>
        <dd><b class="mono">${value}</b><span class="why">${why}</span></dd>
      </div>`)}
    </dl>
    ${config.effective_env == null ? null : html`<p class="hint">Read from the running process.</p>`}
  </section>`;
}

/** What needs a restart, said before it is saved rather than after (draft §7.3). */
export function RestartRequired({ config }) {
  const startup = config.startup || {};
  const rows = RESTART_REQUIRED.map(([key, env, what]) => ({
    key,
    cells: {
      setting: html`<code class="mono">${env}</code>`,
      value: startup[key] === undefined
        ? html`<span class="muted">(not reported)</span>`
        : html`<b class="mono">${String(startup[key])}</b>`,
      what,
    },
  }));
  return html`<section class=${`panel-block param-${STRUCTURAL}`}>
    <${SectionHeading} title="Read at startup — a change needs a restart"
      hint=${"Read once at process start and never mutated. Shown rather than editable: a form " +
             "that accepted a new trap port while the receiver kept the old one would leave an " +
             "operator believing traps were arriving."} />
    ${rows.length ? html`<${DataTable} columns=${[
      { key: "setting", label: "environment variable" },
      { key: "value", label: "value at startup" },
      { key: "what", label: "what it controls" },
    ]} rows=${rows} /> ` : html`<${Empty} title="Nothing reported." will="" meanwhile="" />`}
    <p class="hint">To change one: set the environment variable and restart the process. Nothing
      on this screen can do it, and pretending otherwise is the failure this section exists to
      avoid. ${plural(rows.length, "setting")} are in this class.</p>
  </section>`;
}
