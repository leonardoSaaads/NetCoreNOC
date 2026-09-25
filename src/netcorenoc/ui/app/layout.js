/* The network graph's geometry: **pure, deterministic, and asserted** (v0.22.0, #383).
 *
 * The force drawing it replaces was d3's simulation: it re-laid out between glances, pushed nodes
 * against the edges of its box, and ran against a recording double in the harness, so nothing
 * about it was ever asserted. This module is plain arithmetic the harness calls directly.
 *
 * ## Where a node goes depends on the TOPOLOGY, never on the load
 *
 * Positions are a function of the element ids and the learned edges alone. An element whose alarm
 * count changes does not move; one that gains an edge does. So two glances at an unchanged estate
 * agree, and an operator can compare this morning with now.
 *
 *   1. **Components.** Elements joined by a learned edge form a component; the rest are unlinked.
 *   2. **Each linked component** starts on a circle in id order and runs a fixed number of
 *      Fruchterman–Reingold steps — the same input, the same output, every time.
 *   3. **Packing.** Linked components across the top, largest first; unlinked elements in a grid
 *      band beneath, sorted by name. Unlinked is a fact about the estate, and a band says it.
 *
 * ## Labels
 *
 * A label is HTML at the stylesheet's size, so whether two collide depends on how wide the drawing
 * is. `labels()` answers that for three widths — a phone, a tablet, a desktop — busiest first: a
 * label is shown at a width only if it overlaps no label already placed there and no node. The
 * component turns the answer into classes and a container query picks the width, so the result is
 * deterministic at every width and needs no measurement.
 */

/** Steps of the spring model. Enough to settle a component of a few hundred; fixed for determinism. */
const STEPS = 120;
/** Above this, a component is drawn on its circle: the spring model is O(n²) per step. */
const SPRING_LIMIT = 250;
/** The reference widths, in CSS pixels, a label decision is made for. */
export const WIDTHS = { s: 340, m: 700, l: 1100 };

/** Connected components over the edges whose two ends are both nodes, each sorted by id. */
export function components(ids, edges) {
  const known = new Set(ids);
  const next = new Map(ids.map((id) => [id, []]));
  for (const e of edges) {
    if (known.has(e.a_id) && known.has(e.b_id) && e.a_id !== e.b_id) {
      next.get(e.a_id).push(e.b_id);
      next.get(e.b_id).push(e.a_id);
    }
  }
  const seen = new Set();
  const out = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    const group = [];
    const queue = [id];
    seen.add(id);
    while (queue.length) {
      const at = queue.shift();
      group.push(at);
      for (const peer of next.get(at)) {
        if (!seen.has(peer)) { seen.add(peer); queue.push(peer); }
      }
    }
    out.push(group.sort(byId));
  }
  return out;
}

function byId(a, b) { return String(a).localeCompare(String(b), "en", { numeric: true }); }

/** One component, laid out in a unit box: `{id: [x, y]}` with x, y in [0, 1]. */
export function spring(ids, edges) {
  const n = ids.length;
  if (n === 1) return { [ids[0]]: [0.5, 0.5] };
  const index = new Map(ids.map((id, i) => [id, i]));
  const pos = ids.map((_, i) => [
    0.5 + 0.4 * Math.cos((2 * Math.PI * i) / n),
    0.5 + 0.4 * Math.sin((2 * Math.PI * i) / n),
  ]);
  const links = edges
    .filter((e) => index.has(e.a_id) && index.has(e.b_id) && e.a_id !== e.b_id)
    .map((e) => [index.get(e.a_id), index.get(e.b_id)]);
  if (n <= SPRING_LIMIT) {
    const k = Math.sqrt(1 / n);
    for (let step = 0; step < STEPS; step += 1) {
      const t = 0.1 * (1 - step / STEPS) + 0.002;
      const move = pos.map(() => [0, 0]);
      for (let i = 0; i < n; i += 1) {
        for (let j = i + 1; j < n; j += 1) {
          const dx = pos[i][0] - pos[j][0] || 1e-6 * (i - j);
          const dy = pos[i][1] - pos[j][1] || 1e-6 * (j - i);
          const d2 = dx * dx + dy * dy;
          const f = (k * k) / d2;
          move[i][0] += dx * f; move[i][1] += dy * f;
          move[j][0] -= dx * f; move[j][1] -= dy * f;
        }
      }
      for (const [i, j] of links) {
        const dx = pos[i][0] - pos[j][0];
        const dy = pos[i][1] - pos[j][1];
        const d = Math.sqrt(dx * dx + dy * dy) || 1e-6;
        const f = d / k;
        move[i][0] -= dx * f; move[i][1] -= dy * f;
        move[j][0] += dx * f; move[j][1] += dy * f;
      }
      for (let i = 0; i < n; i += 1) {
        const len = Math.hypot(move[i][0], move[i][1]) || 1;
        const step2 = Math.min(len, t);
        pos[i][0] += (move[i][0] / len) * step2;
        pos[i][1] += (move[i][1] / len) * step2;
      }
    }
  }
  // Normalise into the unit box, keeping the aspect: a triangle stays a triangle.
  const xs = pos.map((p) => p[0]);
  const ys = pos.map((p) => p[1]);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) || 1;
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
  const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const out = {};
  ids.forEach((id, i) => {
    out[id] = [0.5 + (pos[i][0] - cx) / span, 0.5 + (pos[i][1] - cy) / span];
  });
  return out;
}

const round = (v) => Math.round(v * 1000) / 10;

/**
 * **The whole estate's positions**, as percentages of a box whose width:height is `aspect`.
 *
 * Returns `{ nodes: {id: {x, y}}, aspect, linked, unlinked }`, x and y in 0–100. `aspect` is
 * chosen by the caller for its width class; within one class the answer is fixed.
 */
export function layout(nodes, edges, { aspect = 2 } = {}) {
  const ids = nodes.map((node) => node.id).sort(byId);
  const name = new Map(nodes.map((node) => [node.id, String(node.label || node.ip || node.id)]));
  const groups = components(ids, edges);
  const linked = groups.filter((g) => g.length > 1).sort((a, b) => b.length - a.length);
  const unlinked = groups.filter((g) => g.length === 1).map((g) => g[0])
    .sort((a, b) => name.get(a).localeCompare(name.get(b), "en", { numeric: true }) || byId(a, b));
  const out = {};
  // Height shares: the linked band gets the room it needs, the unlinked band a row per ~8.
  // Room for a label beneath each dot: about four to a unit of aspect.
  const perRow = Math.max(3, Math.round(4 * aspect));
  const rows = Math.ceil(unlinked.length / perRow);
  const unlinkedShare = linked.length ? Math.min(0.45, 0.12 * rows) : 1;
  const top = linked.length ? 1 - unlinkedShare : 0;
  if (linked.length) {
    // Components side by side, each given width in proportion to the square root of its size.
    const weights = linked.map((g) => Math.sqrt(g.length));
    const total = weights.reduce((a, b) => a + b, 0);
    let left = 0;
    linked.forEach((group, i) => {
      const width = weights[i] / total;
      const box = spring(group, edges);
      const w = width * aspect;
      const side = Math.min(w, top) * 0.84;
      const ox = left + width / 2;
      for (const id of group) {
        const [ux, uy] = box[id];
        out[id] = {
          x: round(ox + ((ux - 0.5) * side) / aspect),
          y: round(top / 2 + (uy - 0.5) * side),
        };
      }
      left += width;
    });
  }
  unlinked.forEach((id, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const inRow = Math.min(perRow, unlinked.length - row * perRow);
    out[id] = {
      x: round((col + 0.5) / inRow),
      y: round(top + (unlinkedShare * (row + 0.3)) / (Math.max(1, rows) + 0.25)),
      // In the band a label goes BENEATH its dot: side by side, neighbours' labels collide.
      band: true,
    };
  });
  return { nodes: out, aspect, linked: linked.length, unlinked: unlinked.length };
}

/**
 * **Which labels fit at each reference width**, busiest first. Returns `{id: {s, m, l, side}}`
 * where `s`/`m`/`l` say whether the label is shown at that width and `side` is `"r"`, `"l"`,
 * or `"b"` (beneath, in the unlinked band).
 *
 * A label is ~`6.4 px` per character at the stylesheet's 11 px, plus padding; a node is its radius.
 * A label that would overlap one already placed, or any node, at that width is not shown there —
 * the element is still named by its button's accessible name, its tooltip and the panel it opens.
 */
export function labels(nodes, placed, { aspect = 2, radius = () => 8 } = {}) {
  const order = [...nodes].sort((a, b) => (b.active_alarms || 0) - (a.active_alarms || 0)
    || byId(a.id, b.id));
  const out = {};
  for (const node of order) {
    const p = placed[node.id];
    out[node.id] = { side: p?.band ? "b" : p?.x > 70 ? "l" : "r" };
  }
  for (const [key, width] of Object.entries(WIDTHS)) {
    const height = width / aspect;
    const boxes = [];
    const discs = nodes.map((node) => {
      const p = placed[node.id];
      return p ? { x: (p.x / 100) * width, y: (p.y / 100) * height, r: radius(node) } : null;
    }).filter(Boolean);
    for (const node of order) {
      const p = placed[node.id];
      if (!p) continue;
      const x = (p.x / 100) * width;
      const y = (p.y / 100) * height;
      const r = radius(node);
      const w = String(node.label || node.ip || node.id).length * 6.4 + 6;
      const side = out[node.id].side;
      const box = side === "b"
        ? { x0: x - w / 2, x1: x + w / 2, y0: y + r + 1, y1: y + r + 15 }
        : side === "r"
          ? { x0: x + r + 2, x1: x + r + 2 + w, y0: y - 7, y1: y + 7 }
          : { x0: x - r - 2 - w, x1: x - r - 2, y0: y - 7, y1: y + 7 };
      const clear = box.x0 >= 0 && box.x1 <= width
        && !boxes.some((b) => b.x0 < box.x1 && box.x0 < b.x1 && b.y0 < box.y1 && box.y0 < b.y1)
        && !discs.some((d) => d.x + d.r > box.x0 && d.x - d.r < box.x1
          && d.y + d.r > box.y0 && d.y - d.r < box.y1);
      out[node.id][key] = clear;
      if (clear) boxes.push(box);
    }
  }
  return out;
}
