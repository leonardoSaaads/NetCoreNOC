"use strict";
/* NetCoreNOC v0.2.0 UI.
 *
 * Security posture (F1): NO string is ever interpolated into innerHTML. Every value that
 * originates outside this file — device ips, class names/oids, instance strings, varbind
 * values, labels, usernames, token names, audit fields — reaches the DOM only through
 * `text()`/`el(...,{text})`, i.e. document.createTextNode / .textContent. `esc()` is the
 * belt-and-braces escaper for the rare places a string is composed before display. The
 * page also runs under a strict CSP (default-src 'none'; script-src 'self'; ...), so d3
 * is loaded locally and there are no inline scripts, styles, or event handlers.
 */

/* ---------- escaping + safe DOM builders ---------- */
function esc(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
function text(value) { return document.createTextNode(String(value == null ? "" : value)); }
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = String(v);      // external strings: safe
      else if (k === "title") node.title = String(v);           // property, never markup
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, String(v));                     // attributes never execute
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : text(c));
  }
  return node;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function $(id) { return document.getElementById(id); }

/* ---------- session / roles / capabilities ---------- */
// v0.7.0: affordances are gated on the RESOLVED capability set from /api/me, not on role rank.
// An admin may have narrowed what a role holds, and a UI that still offered the control would be
// promising something the server will refuse. `can()` is the single question every gate asks.
const ROLES = { viewer: 0, editor: 1, admin: 2 };
let session = null;                       // { user, role, capabilities: Set, scope }
const can = (cap) => !!(session && session.capabilities.has(cap));
const canEdit = () => can("feedback.write") || can("label.write") || can("situation.close");
const isAdmin = () => session && ROLES[session.role] >= ROLES.admin;

/* ---------- API helper (cookie session; CSRF header on mutations) ---------- */
async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  if (method !== "GET") headers["X-NetCoreNOC-Client"] = "ui";
  if (opts.json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method, headers, credentials: "same-origin",
    body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body,
  });
  if (res.status === 401) { showLogin(); throw new Error("unauthenticated"); }
  if (!res.ok) {
    // Surface the server's precise reason (e.g. why a scoring parameter set was rejected)
    // instead of a bare status code. It only ever reaches the DOM via textContent (F1).
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch { /* not JSON */ }
    throw new Error(detail ? `${res.status}: ${detail}` : String(res.status));
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

/* ---------- login ---------- */
function showLogin() {
  session = null;
  stopStream();
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
}
async function tryResume() {
  try {
    const me = await api("/api/me");
    session = {
      user: me.user, role: me.role,
      capabilities: new Set(me.capabilities || []),
      scope: me.scope || { scoped: false, ne_count: null },
    };
    if (me.must_change_password) { showLogin(); $("pwChange").classList.remove("hidden"); return; }
    enterApp();
  } catch { showLogin(); }
}
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errBox = $("loginErr");
  errBox.textContent = "";
  const body = { username: $("lu").value, password: $("lp").value };
  if (!$("pwChange").classList.contains("hidden")) body.new_password = $("lp2").value;
  try {
    const out = await api("/api/login", { method: "POST", json: body });
    if (out.must_change_password) {
      $("pwChange").classList.remove("hidden");
      errBox.textContent = "Set a new password to continue.";
      return;
    }
    // The login response predates the capability resolution, so ask /api/me for the resolved set
    // rather than assuming role rank implies it.
    const me = await api("/api/me");
    session = {
      user: out.user, role: out.role,
      capabilities: new Set(me.capabilities || []),
      scope: me.scope || { scoped: false, ne_count: null },
    };
    $("login").classList.add("hidden");
    $("pwChange").classList.add("hidden");
    $("lp").value = ""; $("lp2").value = "";
    enterApp();
  } catch {
    errBox.textContent = "Sign-in failed. Check your credentials.";
  }
});
async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
  // Full reload so the next login rebuilds a fresh, correctly role-pruned DOM (A.4).
  location.reload();
}

/* ---------- role-aware navigation ---------- */
const TABS = [
  { id: "situations", label: "Situations", cap: "situations.read" },
  { id: "timeline", label: "Timeline", cap: "timeline.read" },
  { id: "entities", label: "Entities", cap: "entities.read" },
  { id: "users", label: "Users", cap: "users.manage" },
  { id: "tokens", label: "Tokens", cap: "tokens.manage" },
  { id: "config", label: "Config", cap: "config.read" },
  { id: "scorer", label: "Scorer", cap: "scorer.write" },
  { id: "governance", label: "Governance", cap: "rbac.read" },
  { id: "quarantine", label: "Quarantine", cap: "quarantine.read" },
  { id: "audit", label: "Audit", cap: "audit.read" },
];
let activePanel = "situations";
function buildTabs() {
  const nav = $("tabs");
  clear(nav);
  for (const tab of TABS) {
    if (!can(tab.cap)) continue;
    const btn = el("button", {
      class: "tab" + (tab.id === activePanel ? " active" : ""),
      text: tab.label, onclick: () => selectPanel(tab.id),
    });
    nav.append(btn);
  }
}
function selectPanel(id) {
  activePanel = id;
  for (const p of document.querySelectorAll(".panel"))
    p.classList.toggle("active", p.dataset.panel === id);
  for (const b of document.querySelectorAll("nav.tabs button"))
    b.classList.toggle("active", b.textContent === (TABS.find((t) => t.id === id) || {}).label);
  renderPanel(id);
}
function renderPanel(id) {
  if (id === "timeline") loadTimeline();
  else if (id === "entities") loadEntities();
  else if (id === "users") loadUsers();
  else if (id === "tokens") loadTokens();
  else if (id === "config") loadConfig();
  else if (id === "scorer") loadScorer();
  else if (id === "governance") loadGovernance();
  else if (id === "quarantine") loadQuarantine();
  else if (id === "audit") loadAudit();
}

// A.4: a role must not merely be unable to *reach* a screen it lacks — the screen is absent
// from its DOM entirely. Remove every panel whose role the caller does not hold. (Logout does a
// full reload, so a later higher-role login on the same page rebuilds the pruned panels.)
function prunePanels() {
  for (const tab of TABS) {
    if (!can(tab.cap)) {
      const panel = document.querySelector(`.panel[data-panel="${tab.id}"]`);
      if (panel) panel.remove();
    }
  }
}

function enterApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  buildTabs();
  prunePanels();
  const who = $("who");
  clear(who);
  who.append(
    el("span", { class: "role-tag", text: session.role }),
    el("b", { text: session.user }),
    el("button", { text: "Sign out", onclick: logout }),
  );
  // A scoped operator must know their picture is partial, and why. Silence here is what turns a
  // presentation control into a lie during an incident.
  if (session.scope && session.scope.scoped) {
    who.prepend(el("span", {
      class: "scope-tag",
      title: "An administrator has limited which network elements you can see. Situations may "
           + "include members outside your scope; those are shown as a redacted count. Scoping "
           + "hides them from you — it does not stop them correlating.",
      text: `scoped: ${session.scope.ne_count} NE`,
    }));
  }
  selectPanel(activePanel === "situations" ? "situations" : "situations");
  startStream();
  poll();
}

/* ---------- header stats + F6 banner ---------- */
function renderStats(s) {
  const chips = $("chips");
  clear(chips);
  const rows = [
    ["devices", s.devices], ["classes", s.classes], ["active alarms", s.active_alarms],
    ["open situations", s.open_situations], ["p95 latency", (s.latency_p95_s ?? 0) + " s"],
  ];
  for (const [label, value] of rows)
    chips.append(el("span", { class: "chip" }, el("b", { text: value }), text(label)));
  const banner = $("banner");
  const warns = (s.warnings || []);
  if (warns.length && isAdmin()) {
    clear(banner);
    banner.append(el("b", { text: "⚠ " }), text(warns.join("  •  ")));
    banner.classList.add("show");
  } else {
    banner.classList.remove("show");
  }
  // Ingest-gap banner (§5.6): an open gap means traps are being dropped right now — the single
  // most operationally urgent thing NetCoreNOC can say. Closed gaps show as a chip for history.
  const gapBanner = $("gapbanner");
  const open = s.open_ingest_gaps || [];
  if (open.length) {
    clear(gapBanner);
    const lost = open.reduce((n, g) => n + (g.dropped || 0), 0);
    const reasons = [...new Set(open.map((g) => g.reason))].join(", ");
    gapBanner.append(el("b", { text: "⚠ Ingest gap — " }),
      text(`dropping traps now: ${lost} event(s) lost (${reasons}).`));
    gapBanner.classList.add("show");
  } else {
    gapBanner.classList.remove("show");
  }
  const gaps = (s.ingest_gaps || []).length;
  if (gaps) chips.append(el("span", { class: "chip warn-chip" }, el("b", { text: gaps }), text("ingest gaps")));
}

/* ---------- living graph ---------- */
const svg = d3.select("#graph");
const zoomLayer = svg.append("g");
const edgeLayer = zoomLayer.append("g");
const nodeLayer = zoomLayer.append("g");
const labelLayer = zoomLayer.append("g");
svg.call(d3.zoom().scaleExtent([0.3, 4]).on("zoom", (e) => zoomLayer.attr("transform", e.transform)));

const nodesById = new Map();
let links = [];
const sim = d3.forceSimulation()
  .force("charge", d3.forceManyBody().strength(-220))
  .force("link", d3.forceLink().id((d) => d.id).distance(90).strength((l) => 0.2 + 0.5 * l.weight))
  .force("collide", d3.forceCollide(26))
  .on("tick", tick);
function centerForce() {
  const { width, height } = svg.node().getBoundingClientRect();
  sim.force("center", d3.forceCenter(width / 2, height / 2).strength(0.05));
}
window.addEventListener("resize", centerForce);
centerForce();

function displayName(n) { return n.label || n.ip; }

function updateGraph(graph) {
  let changed = false;
  for (const raw of graph.nodes) {
    const existing = nodesById.get(raw.id);
    if (existing) Object.assign(existing, raw);
    else { nodesById.set(raw.id, { ...raw }); changed = true; }
  }
  const newLinks = graph.edges.map((e) => ({ source: e.a_id, target: e.b_id, weight: e.weight, n: e.n }))
    .filter((l) => nodesById.has(l.source) && nodesById.has(l.target));
  if (newLinks.length !== links.length) changed = true;
  links = newLinks;
  const nodes = [...nodesById.values()];

  const nodeSel = nodeLayer.selectAll("circle").data(nodes, (d) => d.id)
    .join((enter) => enter.append("circle")
      .call(d3.drag()
        .on("start", (e, d) => { sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end", (e, d) => { sim.alphaTarget(0); d.fx = null; d.fy = null; }))
      .on("dblclick", (e, d) => { if (canEdit()) rename("device", d.id, displayName(d)); }));
  nodeSel
    .attr("r", (d) => 7 + 2.5 * Math.sqrt(d.active_alarms))
    .attr("class", (d) => "node " + (d.active_alarms > 0 ? "alarm" : "ok"));
  nodeSel.selectAll("title").remove();
  // Tooltip text via .text() (textContent under the hood) — never innerHTML.
  nodeSel.append("title").text((d) =>
    `${displayName(d)}\n${d.vendor || "unknown vendor"}\n${d.active_alarms} active alarm(s)`
    + (canEdit() ? "\ndouble-click to rename" : ""));

  labelLayer.selectAll("text").data(nodes, (d) => d.id)
    .join("text").attr("class", "node-label").text(displayName);

  edgeLayer.selectAll("line").data(links, (d) => `${d.source.id ?? d.source}-${d.target.id ?? d.target}`)
    .join("line").attr("class", "edge")
    .attr("stroke-opacity", (d) => 0.25 + 0.6 * d.weight)
    .attr("stroke-width", (d) => 1 + 2 * d.weight)
    .selectAll("title").data((d) => [d]).join("title")
    .text((d) => `affinity ${d.weight.toFixed(2)} (n=${d.n.toFixed(1)})`);

  sim.nodes(nodes);
  sim.force("link").links(links);
  if (changed) sim.alpha(0.6).restart();
}
function tick() {
  nodeLayer.selectAll("circle").attr("cx", (d) => d.x).attr("cy", (d) => d.y);
  labelLayer.selectAll("text").attr("x", (d) => d.x + 12).attr("y", (d) => d.y + 4);
  edgeLayer.selectAll("line")
    .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
    .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
}

/* ---------- situations ---------- */
const expanded = new Set();
function age(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return Math.round(s) + "s";
  if (s < 5400) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}
// v0.7.1: the three operator writes can now legitimately fail with a 404 — the target no longer
// exists, or it lies outside the caller's visibility scope, which are deliberately the same answer
// (F34/F37). Report it rather than swallowing the rejection: an operator who clicks Close and sees
// nothing happen would reasonably assume it worked. `err.message` is the server's own detail and
// reaches the DOM only through alert/textContent (F1).
async function rename(kind, id, current) {
  const label = prompt(`Rename ${kind} (cosmetic label):`, current);
  if (label === null || !label.trim()) return;
  try {
    await api("/api/labels", { method: "POST", json: { kind, id, label: label.trim() } });
  } catch (err) { alert(`Rename failed — ${err.message}`); }
  poll();
}
async function feedback(sid, verdict) {
  try {
    await api(`/api/situations/${sid}/feedback`, { method: "POST", json: { verdict } });
  } catch (err) { alert(`Feedback failed — ${err.message}`); }
  poll();
}
async function closeSituation(sid) {
  try {
    await api(`/api/situations/${sid}/close`, { method: "POST", json: {} });
  } catch (err) { alert(`Close failed — ${err.message}`); }
  poll();
}
function alarmName(a) { return a.class_label || a.class_name || a.class_oid; }
function deviceName(a) { return a.device_label || a.device_ip; }

// v0.6.0: the explanation comes from the scorer's own named term list. `term_t/term_a/term_e`
// remain as the same three numbers under their legacy names for any older client.
const TERM_CLASS = { temporal: "t", class_affinity: "a", entity_affinity: "e" };
const TERM_SHORT = { temporal: "t", class_affinity: "A", entity_affinity: "E" };
function linkTerms(l) {
  if (Array.isArray(l.terms) && l.terms.length) return l.terms;
  return [
    { name: "temporal", contribution: l.term_t },
    { name: "class_affinity", contribution: l.term_a },
    { name: "entity_affinity", contribution: l.term_e },
  ];
}
function termBar(l) {
  const part = (x) => Math.max(1, Math.round(120 * (x / 0.95)));
  const terms = linkTerms(l);
  const title = terms.map((t) => `${TERM_SHORT[t.name] || t.name}=${t.contribution.toFixed(2)}`).join(" ");
  const bar = el("span", { class: "bar", title });
  for (const t of terms) {
    const fill = el("i", { class: TERM_CLASS[t.name] || "t" });
    fill.style.width = part(t.contribution) + "px";  // CSSOM (allowed by CSP), not inline attr
    bar.append(fill);
  }
  return bar;
}

async function renderDetail(container, sid) {
  const d = await api(`/api/situations/${sid}`);
  const byId = new Map(d.alarms.map((a) => [a.id, a]));
  const root = byId.get(d.root_alarm_id);
  clear(container);

  if (root) {
    container.append(el("div", { class: "root" },
      text("probable root: "), el("b", { text: alarmName(root) }),
      text(" on "), el("b", { text: deviceName(root) }),
      d.root_confidence != null ? text(`  (confidence ${(d.root_confidence * 100).toFixed(0)}%)`) : null));
  }

  // The honest signal that this situation extends past the reader's visibility scope. It carries
  // a count and the alarm classes involved — never an NE id, address, entity key, or varbind.
  if (d.redacted_members) {
    container.append(el("div", { class: "warnbox" },
      el("b", { text: `${d.redacted_members.count} member(s) outside your visibility scope` }),
      text(d.redacted_members.classes.length
        ? "  classes: " + d.redacted_members.classes.join(", ")
        : ""),
      el("div", { class: "hint", text:
        "Scoping hides these members from you; it does not stop them correlating. This situation "
        + "is larger than what is shown here." })));
  }

  const table = el("table");
  table.append(el("tr", null,
    el("th", { text: "device" }), el("th", { text: "class" }), el("th", { text: "instance" }),
    el("th", { text: "severity" }), el("th", { text: "count" }), el("th", { text: "state" })));
  for (const a of d.alarms) {
    const devCell = el("td");
    const devName = el("span", { class: "name", text: deviceName(a) });
    if (canEdit()) devName.addEventListener("click", () => rename("device", a.device_id, deviceName(a)));
    devCell.append(devName);
    const clsCell = el("td");
    const clsName = el("span", { class: "name", text: alarmName(a) });
    if (canEdit()) clsName.addEventListener("click", () => rename("class", a.class_id, alarmName(a)));
    clsCell.append(clsName);
    if (a.is_flapping) clsCell.append(el("span", { class: "flap", text: " ~flapping" }));
    table.append(el("tr", null, devCell, clsCell,
      el("td", { text: a.instance || "—" }), severityCell(a),
      el("td", { text: a.count }), el("td", { text: a.status })));
  }
  container.append(table);

  const linkBox = el("div", { class: "links" });
  const shown = d.links.slice(0, 30);
  if (!shown.length) linkBox.append(el("span", { class: "hint", text: "no links (singleton)" }));
  for (const l of shown) {
    const a = byId.get(l.alarm_a), b = byId.get(l.alarm_b);
    linkBox.append(el("div", { class: "linkrow" }, termBar(l),
      el("span", { text: l.score.toFixed(2) }),
      el("span", { text: `${a ? alarmName(a) : l.alarm_a} ↔ ${b ? alarmName(b) : l.alarm_b}` })));
  }
  container.append(linkBox);

  if (canEdit()) {
    const fb = el("div", { class: "fb" });
    fb.append(
      el("button", { text: "✓ Confirm grouping", onclick: () => feedback(sid, "confirm") }),
      el("button", { class: "warn", text: "✗ Split (wrong grouping)", onclick: () => feedback(sid, "split") }));
    if (d.status === "open") fb.append(el("button", { text: "Close situation", onclick: () => closeSituation(sid) }));
    container.append(fb);
  }
}

function renderSituations(list) {
  const sits = $("sits");
  // v0.7.5 §5.1: a card the operator has OPEN is held — its detail node, and the feedback buttons
  // whose onclick closures live inside it, survive the 2 s SSE rebuild. Before this, `clear(sits)`
  // was the first statement here and destroyed every card including the expanded one, so a click
  // could land on a detached node, or on a button from a render the operator never read — a
  // silently wrong label (FEEDBACK-PATH-0.7.5-DRAFT §1.1).
  // Harvested BEFORE the clear: `clear` detaches nodes, it does not destroy them, so a held detail
  // is re-appended below and keeps its identity and its listeners.
  // Collapsed cards are still cleared and rebuilt: they are cheap and carry no click target the
  // operator is aiming at. This is the narrow fix the draft prefers over a general reconciler.
  const held = new Map();
  for (const card of sits.children) {
    const sid = Number(card.dataset.sid);
    if (expanded.has(sid)) held.set(sid, card.lastChild);
  }
  clear(sits);
  if (!list.length) { sits.append(el("div", { class: "empty", text: "No situations match — the network is quiet." })); return; }
  for (const s of list) {
    const detail = held.get(s.id) || el("div", { class: "detail" });
    detail.style.display = expanded.has(s.id) ? "block" : "none";
    const head = el("div", { class: "sit-head" },
      el("span", { class: "sid", text: "#" + s.id }),
      el("span", { class: "badge " + (s.status === "open" ? "alarm" : ""), text: s.status }),
      el("span", { class: "badge", text: `${s.alarm_count} alarm${s.alarm_count === 1 ? "" : "s"}` }),
      el("span", { class: "age", text: age(s.updated_at) }));
    // A scoped viewer must be told when a situation is bigger than what they are being shown.
    // Omitting this silently would let them size an incident wrongly during the incident.
    if (s.redacted_count) {
      head.insertBefore(el("span", {
        class: "badge redacted",
        title: "Members of this situation are outside your visibility scope and are not shown. "
             + "Scoping hides them from you; it does not stop them correlating.",
        text: `+${s.redacted_count} outside your scope`,
      }), head.lastChild);
    }
    head.addEventListener("click", async () => {
      if (expanded.has(s.id)) { expanded.delete(s.id); detail.style.display = "none"; return; }
      expanded.add(s.id); detail.style.display = "block"; await renderDetail(detail, s.id);
    });
    // `data-sid` is how the next render finds this card's detail node again (see `held` above).
    const card = el("div", { class: "sit", "data-sid": s.id }, head, detail);
    sits.append(card);
    // Only a card that was NOT held needs its detail fetched: a held one already has its content
    // and re-fetching it is precisely the rebuild §5.1 exists to stop. This is where v0.7.4's
    // un-awaited `renderDetail(detail, s.id)` used to fire on every update.
    if (expanded.has(s.id) && !held.has(s.id)) renderDetail(detail, s.id);
  }
}

/* ---------- timeline ---------- */
async function loadTimeline() {
  let data;
  try { data = await api("/api/timeline?limit=300"); } catch { return; }
  const marks = data.marks || [];
  const tl = d3.select("#timeline");
  tl.selectAll("*").remove();
  const box = tl.node().getBoundingClientRect();
  const w = box.width || 500, h = 240, pad = 30;
  if (!marks.length) { $("tlList").replaceChildren(el("div", { class: "empty", text: "No recent alarm activity." })); return; }
  const times = marks.map((m) => m.ts);
  const x = d3.scaleLinear().domain([Math.min(...times), Math.max(...times) + 1]).range([pad, w - pad]);
  const devices = [...new Set(marks.map((m) => m.device))];
  const y = d3.scalePoint().domain(devices).range([pad, h - pad]).padding(0.5);
  const g = tl.attr("viewBox", `0 0 ${w} ${h}`).append("g");
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h - pad})`).call(d3.axisBottom(x).ticks(5).tickFormat((t) => new Date(t * 1000).toLocaleTimeString()));
  g.append("g").attr("class", "axis").attr("transform", `translate(${pad},0)`).call(d3.axisLeft(y));
  g.selectAll("circle").data(marks).join("circle")
    .attr("cx", (m) => x(m.ts)).attr("cy", (m) => y(m.device)).attr("r", 4)
    .attr("class", (m) => m.kind === "clear" ? "tl-clear" : "tl-raise")
    .append("title").text((m) => `${m.device} ${m.class} (${m.kind}) ${new Date(m.ts * 1000).toLocaleString()}`);
  $("tlList").replaceChildren();
}

/* ---------- severity cell (learned, honest unknown fallback) ---------- */
function severityCell(a) {
  const r = a.severity_rank;
  const known = a.severity != null;
  const cls = !known ? "sev-unknown"
    : r === 0 ? "sev-crit" : r === 1 ? "sev-major" : r <= 2 ? "sev-minor" : "sev-low";
  return el("td", {
    class: "sev " + cls,
    title: known ? `rank ${r}` : "severity not learned for this element",
  }, known ? a.severity : "unknown");   // text child -> createTextNode, never markup
}

/* ---------- entities: learned identity, severity, state (viewer+) ---------- */
const entExpanded = new Set();
async function loadEntities() {
  const view = $("entitiesView");
  clear(view);
  let nes, states;
  try { [nes, states] = await Promise.all([api("/api/entities"), api("/api/state-clears")]); }
  catch { return; }
  if (!nes.length) { view.append(el("div", { class: "empty", text: "No network elements yet." })); return; }
  for (const ne of nes) {
    const detail = el("div", { class: "detail" });
    detail.style.display = entExpanded.has(ne.id) ? "block" : "none";
    const head = el("div", { class: "sit-head" },
      el("span", { class: "sid", text: ne.label || ne.ip }),
      el("span", { class: "badge", text: `${ne.entity_count} entit${ne.entity_count === 1 ? "y" : "ies"}` }),
      ne.vendor ? el("span", { class: "age", text: ne.vendor }) : null);
    head.addEventListener("click", async () => {
      if (entExpanded.has(ne.id)) { entExpanded.delete(ne.id); detail.style.display = "none"; return; }
      entExpanded.add(ne.id); detail.style.display = "block"; await renderEntityDetail(detail, ne.id);
    });
    view.append(el("div", { class: "sit" }, head, detail));
    if (entExpanded.has(ne.id)) renderEntityDetail(detail, ne.id);
  }
  if (states.length) {
    view.append(el("h3", { text: "Learned state-clear fields" }));
    view.append(tableFrom(["class", "varbind OID", "raise value", "clear value"],
      states.map((s) => [s.class, el("span", { class: "mono", text: s.varbind_oid }),
        s.raise_value, s.clear_value])));
  }
}
async function renderEntityDetail(container, neId) {
  clear(container);
  let d; try { d = await api(`/api/entities/${neId}`); } catch { return; }
  container.append(el("h4", { text: "Entity tree" }));
  container.append(tableFrom(["level", "key", "key source (OID)", "confidence"],
    d.entities.map((e) => [String(e.level),
      el("span", { class: "name", text: e.key }),
      e.key_source === "self" ? "— (the NE itself)" : el("span", { class: "mono", text: e.key_source }),
      e.confidence != null ? (e.confidence * 100).toFixed(0) + "%" : "—"])));
  container.append(el("h4", { text: "Varbind profiler — the evidence behind the choice" }));
  container.append(tableFrom(["varbind OID", "R", "X", "D", "score", "obs", "distinct", "promotable"],
    d.candidates.map((c) => [el("span", { class: "mono", text: c.varbind_oid }),
      c.r.toFixed(2), c.x.toFixed(2), c.d.toFixed(2), el("b", { text: c.score.toFixed(2) }),
      String(c.n_obs), String(c.n_distinct),
      c.meets_floor ? el("span", { class: "outcome-ok", text: "yes" })
        : el("span", { class: "hint", text: "no" })])));
  if (isAdmin()) {
    const box = el("div", { class: "fb" });
    box.append(
      el("button", {
        text: "Reset identity decision",
        title: "Forget the learned entity/severity; re-decide from current evidence. History kept.",
        onclick: async () => {
          if (!confirm("Forget this NE's learned entity/severity decision? History is kept.")) return;
          await api(`/api/entities/${neId}/reset`, { method: "POST", json: {} });
          renderEntityDetail(container, neId);
        },
      }),
      el("button", {
        class: "warn",
        text: "Wipe profiler evidence",
        title: "Also drop the accumulated evidence, so identity/severity re-measure from scratch.",
        onclick: async () => {
          if (!confirm("Wipe this NE's profiler evidence and re-measure from scratch?")) return;
          await api(`/api/profiles/${neId}/reset`, { method: "POST", json: {} });
          renderEntityDetail(container, neId);
        },
      }));
    container.append(box);
  }
}

/* ---------- admin views ---------- */
function tableFrom(headers, rows) {
  const t = el("table", { class: "adm" });
  t.append(el("tr", null, ...headers.map((h) => el("th", { text: h }))));
  for (const cells of rows) t.append(el("tr", null, ...cells.map((c) => c.nodeType ? el("td", null, c) : el("td", { text: c }))));
  return t;
}
async function loadUsers() {
  const view = $("usersView"); clear(view);
  let users; try { users = await api("/api/users"); } catch { return; }
  const rows = users.map((u) => [u.username, el("span", { class: "role-tag", text: u.role }),
    u.disabled ? "disabled" : "active",
    el("button", { text: "Delete", class: "warn", onclick: async () => { if (confirm(`Delete ${u.username}?`)) { await api(`/api/users/${u.id}`, { method: "DELETE" }); loadUsers(); } } })]);
  view.append(tableFrom(["user", "role", "state", ""], rows));
  const form = el("form", { class: "filters" });
  const nu = el("input", { placeholder: "username" });
  const np = el("input", { placeholder: "password (12+)", type: "password" });
  const nr = el("select"); ["viewer", "editor", "admin"].forEach((r) => nr.append(el("option", { value: r, text: r })));
  form.append(nu, np, nr, el("button", { text: "Add user" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/users", { method: "POST", json: { username: nu.value, password: np.value, role: nr.value } }); nu.value = ""; np.value = ""; loadUsers(); }
    catch { alert("Could not create user (check password length / duplicate name)."); }
  });
  view.append(form);
}
async function loadTokens() {
  const view = $("tokensView"); clear(view);
  let tokens; try { tokens = await api("/api/tokens"); } catch { return; }
  const rows = tokens.map((t) => [t.name, el("span", { class: "role-tag", text: t.role }),
    t.revoked ? "revoked" : "active",
    t.revoked ? "" : el("button", { text: "Revoke", class: "warn", onclick: async () => { await api(`/api/tokens/${t.id}`, { method: "DELETE" }); loadTokens(); } })]);
  view.append(tableFrom(["name", "role", "state", ""], rows));
  const form = el("form", { class: "filters" });
  const tn = el("input", { placeholder: "token name" });
  const tr = el("select"); ["viewer", "editor", "admin"].forEach((r) => tr.append(el("option", { value: r, text: r })));
  form.append(tn, tr, el("button", { text: "Create token" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const out = await api("/api/tokens", { method: "POST", json: { name: tn.value, role: tr.value } });
      tn.value = "";
      view.append(el("div", { class: "tokval" }, el("div", { text: "Copy now — shown once:" }), el("div", { class: "mono", text: out.token })));
      loadTokens();
    } catch { alert("Could not create token (duplicate name?)."); }
  });
  view.append(form);
}
async function loadConfig() {
  const view = $("configView"); clear(view);
  let cfg; try { cfg = await api("/api/config"); } catch { return; }
  const form = el("form");
  const al = el("input", { value: cfg.allowlist || "", placeholder: "e.g. 10.0.0.0/8,192.168.1.0/24" });
  const rt = el("input", { value: cfg.retention_days, type: "number", step: "0.1" });
  form.append(el("label", { text: "Trap allowlist (CIDRs, comma-separated; empty = allow all)" }), al,
    el("label", { text: "Retention days" }), rt, el("button", { text: "Save" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/config", { method: "POST", json: { allowlist: al.value, retention_days: parseFloat(rt.value) } });
    alert("Saved. Changes are audited."); loadConfig();
  });
  view.append(form);
}
/* ---------- admin: the link scorer (v0.6.0) ----------
 * Read is viewer+ on the API (the parameters EXPLAIN grouping), but the panel is admin-gated
 * because everything it offers beyond reading — preview, apply, rollback — is admin-only and
 * is pruned from a non-admin DOM entirely (prunePanels, A.4).
 * Every value below reaches the DOM through el({text})/text(), never innerHTML (F1). */
const SCORER_FIELDS = [
  ["w_t", "w_t — temporal weight"],
  ["w_a", "w_a — class-affinity weight"],
  ["w_e", "w_e — entity-affinity weight"],
  ["tau_s", "tau (s) — temporal decay constant"],
  ["threshold", "threshold — link when score exceeds this"],
];
async function loadScorer() {
  const view = $("scorerView"); clear(view);
  let cfg; try { cfg = await api("/api/scorer"); } catch { return; }

  const active = el("div", { class: "scorer-active" },
    el("div", null, text("active: "), el("b", { text: cfg.scorer_id }),
      text(` (contract ${cfg.contract_version}, config #${cfg.config_id ?? "—"})`)),
    el("div", { class: "mono", text: SCORER_FIELDS.map(([k]) => `${k}=${cfg.params[k]}`).join("  ") }));
  if (cfg.degraded) {
    active.append(el("div", { class: "warn",
      text: `Degraded: ${cfg.degraded_reason} — running on the built-in defaults.` }));
  }
  view.append(active);

  const form = el("form");
  const inputs = {};
  for (const [key, label] of SCORER_FIELDS) {
    inputs[key] = el("input", { value: cfg.params[key], type: "number", step: "0.01" });
    form.append(el("label", { text: label }), inputs[key]);
  }
  const note = el("input", { placeholder: "why are you changing this? (recorded in the audit log)" });
  form.append(el("label", { text: "Note" }), note);
  const bounds = cfg.bounds;
  form.append(el("div", { class: "hint", text:
    `Bounds: weights and threshold in [0, 1]; tau in [${bounds.min_tau_s}, ${bounds.max_tau_s}] s; ` +
    `weights must sum to at least ${bounds.min_weight_sum}; the threshold must stay at least ` +
    `${bounds.threshold_margin} below the weight sum. Values outside these are rejected — they ` +
    `would merge every alarm into one situation or stop grouping entirely.` }));

  const out = el("div", { class: "scorer-preview" });
  const read = () => {
    const body = { note: note.value };
    for (const [key] of SCORER_FIELDS) body[key] = parseFloat(inputs[key].value);
    return body;
  };
  const previewBtn = el("button", { type: "button", text: "Preview effect", onclick: async () => {
    clear(out);
    out.append(el("div", { class: "hint", text: "Running…" }));
    let delta;
    try { delta = await api("/api/scorer/preview", { method: "POST", json: read() }); }
    catch (err) { clear(out); out.append(el("div", { class: "warn", text: `Preview failed: ${err.message}` })); return; }
    renderPreview(out, delta);
  } });
  const applyBtn = el("button", { text: "Apply" });
  form.append(el("div", { class: "fb" }, previewBtn, applyBtn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!confirm("Apply these scoring parameters? Grouping will change at the next engine reload.")) return;
    try { await api("/api/scorer", { method: "POST", json: read() }); }
    catch (err) { alert(`Rejected: ${err.message}`); return; }
    loadScorer();
  });
  view.append(form);
  view.append(out);

  const history = cfg.history || [];
  view.append(el("h3", { text: "History" }));
  view.append(el("div", { class: "hint", text:
    "Immutable and append-only: nothing here is ever edited or deleted. Rolling back moves the " +
    "active pointer to an earlier row." }));
  view.append(tableFrom(["#", "params", "by", "when", "note", ""],
    history.map((h) => [
      h.active ? el("b", { text: `${h.id} (active)` }) : String(h.id),
      el("span", { class: "mono", text: `${h.w_t}/${h.w_a}/${h.w_e} tau=${h.tau_s} thr=${h.threshold}` }),
      h.created_by || "—",
      h.created_at ? new Date(h.created_at * 1000).toLocaleString() : "—",
      h.note || "—",
      h.active ? "" : el("button", { text: "Roll back", onclick: async () => {
        if (!confirm(`Roll back to configuration #${h.id}?`)) return;
        await api("/api/scorer/rollback", { method: "POST", json: { config_id: h.id } });
        loadScorer();
      } }),
    ])));
}
function renderPreview(out, d) {
  clear(out);
  out.append(el("h3", { text: "Preview" }));
  // The caveat is a control, not decoration (SECURITY-REVIEW-0.6 §4): a preview that looked
  // authoritative would be worse than none.
  out.append(el("div", { class: "hint", text: d.caveat }));
  out.append(tableFrom(["", "situations", "links"], [
    ["now", String(d.situations_before), String(d.links_before)],
    ["with these parameters", el("b", { text: String(d.situations_after) }), String(d.links_after)],
  ]));
  const merged = d.merged || [], split = d.split || [];
  out.append(el("div", { class: "hint", text:
    `${merged.length} group(s) would merge, ${split.length} would split, ` +
    `${d.unchanged_groups} unchanged, over ${d.alarms_considered} recent alarm(s) ` +
    `(cap ${d.alarms_cap}).` }));
  for (const m of merged.slice(0, 10))
    out.append(el("div", { class: "mono", text: `merge: ${m.from_groups.length} groups -> ${m.size} alarms` }));
  for (const s of split.slice(0, 10))
    out.append(el("div", { class: "mono", text: `split: ${s.size} alarms -> sizes ${s.into_sizes.join(", ")}` }));
}

/* ---------- admin: governance (v0.7.0) ----------
 * Two stored policies, one screen. Both are edited as JSON because a policy is a small,
 * reviewable document and a form would hide its shape; both show what the policy actually
 * RESOLVES to, because "what did I just do?" is the question an admin needs answered.
 *
 * Every string that reaches the DOM goes through textContent (F1) — a policy is operator-supplied
 * text and is never trusted markup.
 */
async function loadGovernance() {
  const view = $("governanceView"); clear(view);
  let caps, scope;
  try {
    caps = await api("/api/rbac");
    scope = await api("/api/scope");
  } catch { return; }

  view.append(el("div", { class: "hint", text:
    "Restrict what each role or principal may DO (capabilities) and SEE (network elements). "
    + "With no policy stored, NetCoreNOC behaves exactly as it does today — most operators never "
    + "need this screen." }));

  /* --- capabilities --- */
  const capBox = el("div", { class: "govbox" });
  capBox.append(el("h3", { text: "Capabilities" }));
  capBox.append(el("div", { class: "hint", text:
    "A policy can only take capabilities away, never add them: each role's compiled ceiling is "
    + "the maximum it may ever hold, so an entry above the ceiling has no effect. An admin always "
    + "keeps the capabilities needed to repair this screen." }));
  if (caps.malformed) {
    capBox.append(el("div", { class: "err", text:
      "The stored capability policy could not be read (" + caps.malformed_reason + "). "
      + "Authorization has fallen back to the built-in role permissions — nobody has gained "
      + "anything. Fix or clear it below." }));
  }
  for (const role of ["viewer", "editor", "admin"]) {
    const resolved = caps.resolved[role] || [];
    const ceiling = caps.ceiling[role] || [];
    const removed = ceiling.filter((c) => !resolved.includes(c));
    capBox.append(el("div", { class: "govrole" },
      el("b", { text: role }),
      el("span", { class: "mono", text: ` ${resolved.length}/${ceiling.length} capabilities` }),
      removed.length
        ? el("span", { class: "muted", text: "  removed: " + removed.join(", ") })
        : el("span", { class: "muted", text: "  (full ceiling)" }),
    ));
  }
  const capText = el("textarea", { class: "govjson", rows: "8" });
  capText.value = caps.active ? caps.active.document
    : JSON.stringify({ version: 1, roles: {}, principals: {} }, null, 2);
  capBox.append(capText);
  const capErr = el("div", { class: "err" });
  capBox.append(el("div", { class: "govactions" },
    el("button", { text: "Apply", onclick: () => writePolicy("rbac", capText, capErr) }),
    el("button", { text: "Clear policy", onclick: () => clearPolicy("rbac", capErr) }),
  ), capErr);
  capBox.append(historyTable(caps.history, "rbac"));
  view.append(capBox);

  /* --- visibility scope --- */
  const scopeBox = el("div", { class: "govbox" });
  scopeBox.append(el("h3", { text: "Visibility scope" }));
  scopeBox.append(el("div", { class: "hint", text:
    "Which network elements a viewer or editor may see. Selectors: ne:<id>, an exact address, a "
    + "CIDR, or an address glob (10.0.*). A selector never matches the operator label, which the "
    + "scoped role can itself write. Admins are never scoped, so a mistake here is repairable." }));
  scopeBox.append(el("div", { class: "warnbox", text:
    "Visibility scoping is a presentation control and is NOT tenant isolation. Correlation still "
    + "learns across every network element, and a situation may still form across a boundary a "
    + "principal cannot see — its members are then hidden from them, shown as a redacted count, "
    + "not prevented from correlating." }));
  if (scope.malformed) {
    scopeBox.append(el("div", { class: "err", text:
      "The stored scope could not be read (" + scope.malformed_reason + "). Viewers and editors "
      + "are seeing nothing until it is fixed or cleared." }));
  }
  for (const role of ["viewer", "editor"]) {
    const ids = (scope.resolved_ne_ids || {})[role] || [];
    scopeBox.append(el("div", { class: "govrole" },
      el("b", { text: role }),
      el("span", { class: "mono", text: scope.configured
        ? ` sees ${ids.length} of ${scope.ne_count} NE`
        : ` sees all ${scope.ne_count} NE (no policy)` }),
    ));
  }
  const scopeText = el("textarea", { class: "govjson", rows: "8" });
  scopeText.value = scope.active ? scope.active.document
    : JSON.stringify({ version: 1, roles: {}, principals: {} }, null, 2);
  scopeBox.append(scopeText);
  const scopeErr = el("div", { class: "err" });
  scopeBox.append(el("div", { class: "govactions" },
    el("button", { text: "Apply", onclick: () => writePolicy("scope", scopeText, scopeErr) }),
    el("button", { text: "Clear policy", onclick: () => clearPolicy("scope", scopeErr) }),
  ), scopeErr);
  scopeBox.append(historyTable(scope.history, "scope"));
  view.append(scopeBox);
}

function historyTable(history, kind) {
  const wrap = el("div", { class: "govhistory" });
  wrap.append(el("div", { class: "hint", text:
    "History is append-only: rolling back moves a pointer and never edits or deletes a version." }));
  wrap.append(tableFrom(["version", "by", "when", "note", ""],
    (history || []).map((h) => [
      el("span", { class: "mono", text: String(h.id) + (h.active ? " (active)" : "") }),
      h.created_by || "—",
      new Date(h.created_at * 1000).toLocaleString(),
      h.note || "",
      h.active ? "" : el("button", { text: "Roll back", onclick: () => rollbackPolicy(kind, h.id) }),
    ])));
  return wrap;
}

async function writePolicy(kind, textarea, errBox) {
  errBox.textContent = "";
  let document_;
  try { document_ = JSON.parse(textarea.value); }
  catch (e) { errBox.textContent = "Not valid JSON: " + e.message; return; }
  try {
    await api("/api/" + kind, { method: "POST", json: { document: document_, note: "" } });
    loadGovernance();
  } catch (e) { errBox.textContent = e.message; }
}

async function clearPolicy(kind, errBox) {
  errBox.textContent = "";
  try {
    await api("/api/" + kind, { method: "POST", json: { clear: true } });
    loadGovernance();
  } catch (e) { errBox.textContent = e.message; }
}

async function rollbackPolicy(kind, policyId) {
  try {
    await api("/api/" + kind, { method: "POST", json: { policy_id: policyId } });
    loadGovernance();
  } catch { /* surfaced by the panel reload */ }
}

async function loadQuarantine() {
  const view = $("quarantineView"); clear(view);
  let rows; try { rows = await api("/api/quarantine?limit=100"); } catch { return; }
  view.append(tableFrom(["source", "reason", "sha256", "len", "received"],
    rows.map((q) => [q.source, q.reason, el("span", { class: "mono", text: q.sha256 }), q.length,
      new Date(q.received_at * 1000).toLocaleString()])));
}
async function loadAudit() {
  const view = $("auditView"); clear(view);
  let rows; try { rows = await api("/api/audit?limit=200"); } catch { return; }
  view.append(el("div", { class: "filters" },
    el("button", { text: "Export NDJSON", onclick: async () => {
      const nd = await api("/api/audit/export"); const blob = new Blob([nd], { type: "application/x-ndjson" });
      const a = el("a", { href: URL.createObjectURL(blob), download: "audit.ndjson" }); a.click();
    } })));
  view.append(tableFrom(["ts", "actor", "role", "action", "object", "outcome"],
    rows.map((r) => [new Date(r.ts * 1000).toLocaleString(), r.actor, r.role || "—", r.action,
      `${r.object_type || ""}${r.object_id ? "/" + r.object_id : ""}`,
      el("span", { class: "outcome-" + r.outcome, text: r.outcome })])));
}

/* ---------- live updates: SSE primary, polling fallback ---------- */
let evtSource = null;
let pollTimer = null;
function applyUpdate(u) {
  if (u.stats) renderStats(u.stats);
  if (u.graph) updateGraph(u.graph);
  if (u.situations && activePanel === "situations") renderSituations(filterSituations(u.situations));
  $("dot").className = "ok live";
}
function startStream() {
  stopStream();
  try {
    evtSource = new EventSource("/api/events");
    evtSource.addEventListener("update", (e) => { try { applyUpdate(JSON.parse(e.data)); } catch { /* ignore */ } });
    evtSource.onerror = () => { stopStream(); startPolling(); };   // fallback
  } catch { startPolling(); }
}
function stopStream() {
  if (evtSource) { evtSource.close(); evtSource = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(poll, 2500);
}
let lastSituations = [];
function filterSituations(list) {
  lastSituations = list;
  const q = ($("fltText").value || "").toLowerCase().trim();
  if (!q) return list;
  return list.filter((s) => (`#${s.id} ${s.status}`).toLowerCase().includes(q));
}
$("fltText").addEventListener("input", () => renderSituations(filterSituations(lastSituations)));
$("fltStatus").addEventListener("change", poll);
async function poll() {
  if (!session) return;
  try {
    const status = $("fltStatus").value;
    const [stats, graph, sits] = await Promise.all([
      api("/api/stats"), api("/api/graph"),
      api(`/api/situations?limit=50${status ? "&status=" + status : ""}`),
    ]);
    renderStats(stats); updateGraph(graph);
    if (activePanel === "situations") renderSituations(filterSituations(sits));
    if (!evtSource) $("dot").className = "ok";
  } catch { $("dot").className = "err"; }
}

/* ---------- boot ---------- */
tryResume();
