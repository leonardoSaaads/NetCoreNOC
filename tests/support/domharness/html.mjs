/* A small, strict HTML parser — the harness's only text-to-DOM path.
 *
 * It exists for two reasons, and the second is the one that matters.
 *
 *   1. `index.html` has to become a tree before `app.js` can be evaluated against it.
 *   2. It backs the micro-DOM's `innerHTML` **setter**. Without a real parser there, the
 *      "no render path writes unescaped data into the document" invariant would be vacuous:
 *      a harness whose only element-creation path is `createElement` cannot construct an
 *      injected `<script>` however badly the code under test behaves, so the test would pass
 *      for a property of the instrument rather than a property of `app.js`. With the parser
 *      wired to `innerHTML`, an injected `node.innerHTML = hostileValue` really does build
 *      elements, and the invariant really does go red. That injection is demonstrated in
 *      `docs/gates/v0.12.0-guard-demonstrations.md` §2.
 *
 * Deliberately not a conforming HTML5 parser. It has no error recovery, no implied tags, no
 * `<table>` fostering, no entity table beyond the five `esc()` produces. It THROWS on input it
 * does not understand rather than guessing, because a parser that silently recovers would let
 * the instrument disagree with a browser without saying so.
 */

const VOID = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr",
]);

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " };

export function decodeEntities(text) {
  return text.replace(/&(#?\w+);/g, (whole, name) =>
    Object.hasOwn(ENTITIES, name) ? ENTITIES[name] : whole);
}

/** Parse `source` into `{ tag, attrs, children }` nodes plus `{ text }` nodes. */
export function parseHTML(source) {
  let i = 0;
  const roots = [];
  const stack = [];
  const push = (node) => (stack.length ? stack[stack.length - 1].children : roots).push(node);

  while (i < source.length) {
    const lt = source.indexOf("<", i);
    if (lt === -1) {
      addText(source.slice(i));
      break;
    }
    if (lt > i) addText(source.slice(i, lt));

    if (source.startsWith("<!--", lt)) {                       // comment: dropped
      const end = source.indexOf("-->", lt);
      if (end === -1) throw new Error("unterminated comment");
      i = end + 3;
      continue;
    }
    if (source.startsWith("<!", lt)) {                         // doctype: dropped
      const end = source.indexOf(">", lt);
      if (end === -1) throw new Error("unterminated doctype");
      i = end + 1;
      continue;
    }
    if (source.startsWith("</", lt)) {                         // closing tag
      const end = source.indexOf(">", lt);
      if (end === -1) throw new Error("unterminated close tag");
      const name = source.slice(lt + 2, end).trim().toLowerCase();
      // Pop to the matching open element. An unmatched close is a bug in the fixture, not
      // something to recover from.
      let depth = stack.length - 1;
      while (depth >= 0 && stack[depth].tag !== name) depth -= 1;
      if (depth < 0) throw new Error(`close tag </${name}> matches no open element`);
      stack.length = depth;
      i = end + 1;
      continue;
    }

    const parsed = parseOpenTag(source, lt);                   // opening tag
    const node = { tag: parsed.tag, attrs: parsed.attrs, children: [] };
    push(node);
    if (!parsed.selfClosing && !VOID.has(parsed.tag)) stack.push(node);
    i = parsed.end;

    if (parsed.tag === "script" || parsed.tag === "style") {
      // Raw-text elements: their content is never markup. Consume to the close tag verbatim.
      const close = source.toLowerCase().indexOf(`</${parsed.tag}`, i);
      if (close === -1) throw new Error(`unterminated <${parsed.tag}>`);
      if (close > i) node.children.push({ text: source.slice(i, close) });
      stack.pop();
      i = source.indexOf(">", close) + 1;
    }
  }
  return roots;

  function addText(raw) {
    if (!raw) return;
    push({ text: decodeEntities(raw) });
  }
}

function parseOpenTag(source, start) {
  const nameMatch = /^<([a-zA-Z][\w-]*)/.exec(source.slice(start));
  if (!nameMatch) throw new Error(`not a tag at offset ${start}: ${source.slice(start, start + 24)}`);
  const tag = nameMatch[1].toLowerCase();
  let i = start + nameMatch[0].length;
  const attrs = {};

  for (;;) {
    while (i < source.length && /\s/.test(source[i])) i += 1;
    if (i >= source.length) throw new Error(`unterminated <${tag}>`);
    if (source[i] === ">") return { tag, attrs, selfClosing: false, end: i + 1 };
    if (source.startsWith("/>", i)) return { tag, attrs, selfClosing: true, end: i + 2 };

    const attrMatch = /^([^\s=/>]+)/.exec(source.slice(i));
    if (!attrMatch) throw new Error(`bad attribute in <${tag}> at offset ${i}`);
    const name = attrMatch[1].toLowerCase();
    i += attrMatch[0].length;
    while (i < source.length && /\s/.test(source[i])) i += 1;

    if (source[i] !== "=") {                                   // bare attribute
      attrs[name] = "";
      continue;
    }
    i += 1;
    while (i < source.length && /\s/.test(source[i])) i += 1;
    const quote = source[i];
    if (quote === '"' || quote === "'") {
      const end = source.indexOf(quote, i + 1);
      if (end === -1) throw new Error(`unterminated attribute value in <${tag}>`);
      attrs[name] = decodeEntities(source.slice(i + 1, end));
      i = end + 1;
    } else {
      const end = /[\s>]/.exec(source.slice(i));
      const stop = end ? i + end.index : source.length;
      attrs[name] = decodeEntities(source.slice(i, stop));
      i = stop;
    }
  }
}
