/* What an Ollama server has, and which of it can work a case (ADR #477).
 *
 * The server's own answer (`POST /api/noc/secrets/{id}/models` → `details`), ranked by the
 * appliance: a model that **calls tools** can work a case (an agent acts through the NOC's tools),
 * one that only chats cannot, an embedding model cannot even answer. The first row is the one to
 * try; it says so, and why. A verdict is a word and an icon, never a colour alone.
 */

import { html, cx } from "../../dom.js";
import { Icon } from "../../icons.js";

const VERDICT = {
  agents: ["check", "Works cases"],
  unknown: ["info", "Not said"],
  chat: ["warn", "No tools"],
  embedding: ["warn", "Embeddings"],
};

/** `4920753328` → "4.9 GB"; `131072` → "128k". */
export function gigabytes(bytes) {
  return bytes == null ? "" : `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;
}

function tokens(n) {
  return n == null ? "—" : n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);
}

/** The sentence above the table: what to pick, or what to pull when nothing fits. */
export function ModelAdvice({ answer, provider }) {
  const rows = answer.details;
  const fit = rows.filter((m) => m.fit === "agents");
  const best = rows.find((m) => m.name === answer.recommended);
  const loaded = rows.filter((m) => m.loaded).length;
  return html`<div class="ollama-advice">
    <p class="muted">${rows.length} model${rows.length === 1 ? "" : "s"} · ${fit.length} can work cases${
      provider.own_host ? ` · ${loaded} loaded now` : ""}.</p>
    ${best ? html`<p class="ollama-reco"><${Icon} name="sparkle" /> Try <code>${best.name}</code> first:
        it calls tools${best.loaded ? " and is loaded now" : ""}${best.parameter_size
          ? `, ${best.parameter_size} parameters` : ""}. A larger model follows a case more steadily; a
        smaller one answers sooner on the same hardware.</p>`
      : html`<p class="warnbox">None of these can call tools, so none can work a case. On that machine,
        pull one that can, e.g. <code>ollama pull ${provider.example_model}</code>, then ask again.</p>`}
  </div>`;
}

/** The models, one row each; `onPick` makes each name a button (the hiring form). */
export function OllamaModels({ answer, provider, onPick, picked }) {
  return html`<div class="table-scroll"><table class="data ollama-models">
    <thead><tr><th scope="col">Model</th><th scope="col">Size</th><th scope="col" class="wide-only">Context</th>
      <th scope="col">Can</th><th scope="col">For an agent</th></tr></thead>
    <tbody>${answer.details.map((m) => {
      const [icon, word] = VERDICT[m.fit] || VERDICT.unknown;
      return html`<tr key=${m.name} class=${cx(picked === m.name && "row-selected")}>
        <td>${onPick ? html`<button type="button" class="linkish mono" aria-pressed=${picked === m.name}
              onClick=${() => onPick(m.name)}>${m.name}</button>` : html`<code>${m.name}</code>`}
          ${m.loaded ? html` <span class="badge" title="In memory on that machine now">loaded</span>` : null}</td>
        <td class="ollama-size">${[m.parameter_size, m.quantization, provider.own_host ? gigabytes(m.size_bytes) : ""]
          .filter(Boolean).join(" · ") || "—"}</td>
        <td class="wide-only">${tokens(m.context_length)}</td>
        <td>${m.capabilities ? html`<span class="ollama-caps">${m.capabilities.map((c) => html`<span
            key=${c} class="chip-static">${c}</span>`)}</span>` : html`<span class="muted">—</span>`}</td>
        <td><span class=${cx("ollama-fit", `ollama-fit-${m.fit}`)} title=${m.why}><${Icon} name=${icon} /> ${word}</span></td>
      </tr>`;
    })}</tbody></table></div>`;
}
