/* Markdown an agent wrote, drawn as what it means (ADR #509).
 *
 * Models answer in Markdown — `## Findings`, `**down**`, `` `show int` ``, lists — and printed as
 * text it read as symbols. This draws the part of CommonMark an answer uses: headings, paragraphs,
 * bold, italic, inline code, fenced code, bullet and numbered lists, block quotes, a rule and links.
 *
 * **Never markup.** Every piece becomes an element built here through Preact, its text a text node:
 * no `innerHTML`, no HTML passes through (an `<img onerror>` in an answer is shown as those
 * characters), and a link is kept only when it is `http(s)` or `mailto`, opened in a new tab with
 * no referrer. What this does not understand is shown as the text it was.
 */

import { html } from "./dom.js";

const SAFE_URL = /^(https?:\/\/|mailto:)[^\s<>"']+$/i;

/** Inline spans: `code`, **bold**, *italic* / _italic_, [text](url). */
export function inline(text, key = "i") {
  const out = [];
  const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*|__[^_\n]+__)|(\*[^*\s][^*\n]*\*|_[^_\s][^_\n]*_)|(\[[^\]\n]+\]\([^)\s]+\))/g;
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const token = m[0];
    const k = `${key}-${n++}`;
    if (m[1]) out.push(html`<code key=${k} class="md-code">${token.slice(1, -1)}</code>`);
    else if (m[2]) out.push(html`<strong key=${k}>${inline(token.slice(2, -2), k)}</strong>`);
    else if (m[3]) out.push(html`<em key=${k}>${inline(token.slice(1, -1), k)}</em>`);
    else {
      const [, label, url] = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      out.push(SAFE_URL.test(url)
        ? html`<a key=${k} href=${url} target="_blank" rel="noopener noreferrer">${inline(label, k)}</a>`
        : token);
    }
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Split the source into blocks: fences, headings, rules, quotes, lists and paragraphs. */
function blocks(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const fence = line.match(/^\s*(```|~~~)\s*([\w+-]*)\s*$/);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i += 1;
      out.push({ kind: "code", text: body.join("\n"), lang: fence[2] });
      continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (heading) { out.push({ kind: "h", level: heading[1].length, text: heading[2] }); i += 1; continue; }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push({ kind: "hr" }); i += 1; continue; }
    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ kind: "quote", text: body.join("\n") });
      continue;
    }
    const item = line.match(/^\s*([-*+]|\d{1,3}[.)])\s+(.*)$/);
    if (item) {
      const ordered = /\d/.test(item[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*+]|\d{1,3}[.)])\s+(.*)$/);
        if (m && /\d/.test(m[1]) === ordered) { items.push(m[2]); i += 1; }
        else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && items.length) {
          items[items.length - 1] += ` ${lines[i].trim()}`; i += 1;
        } else break;
      }
      out.push({ kind: ordered ? "ol" : "ul", items, start: ordered ? parseInt(item[1], 10) : 1 });
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(```|~~~|#{1,6}\s|>|[-*+]\s|\d{1,3}[.)]\s)/.test(lines[i])) {
      para.push(lines[i++].trim());
    }
    if (!para.length) para.push(lines[i++].trim());
    out.push({ kind: "p", text: para.join("\n") });
  }
  return out;
}

/** A paragraph's soft line breaks stay breaks: an agent's lines are usually meant as lines. */
function lines(text, key) {
  const parts = text.split("\n");
  return parts.flatMap((part, n) => (n ? [html`<br key=${`${key}-br${n}`} />`, ...inline(part, `${key}-${n}`)]
    : inline(part, `${key}-${n}`)));
}

/** Markdown as elements, in a `<div class="md">`. */
export function Markdown({ text, className = "" }) {
  return html`<div class=${`md ${className}`.trim()}>
    ${blocks(text).map((b, n) => {
      const k = `b${n}`;
      if (b.kind === "code") return html`<pre key=${k} class="md-pre"><code>${b.text}</code></pre>`;
      if (b.kind === "hr") return html`<hr key=${k} />`;
      if (b.kind === "quote") return html`<blockquote key=${k}>${lines(b.text, k)}</blockquote>`;
      if (b.kind === "ul") return html`<ul key=${k}>${b.items.map((t, j) => html`<li key=${j}>${inline(t, `${k}-${j}`)}</li>`)}</ul>`;
      if (b.kind === "ol") return html`<ol key=${k} start=${b.start}>${b.items.map((t, j) => html`<li key=${j}>${inline(t, `${k}-${j}`)}</li>`)}</ol>`;
      if (b.kind === "h") {
        const level = Math.min(6, b.level + 2);  // inside a card: an h1 reads as an h3
        return html`<p key=${k} class=${`md-h md-h${b.level}`} role="heading" aria-level=${level}>${inline(b.text, k)}</p>`;
      }
      return html`<p key=${k}>${lines(b.text, k)}</p>`;
    })}
  </div>`;
}
