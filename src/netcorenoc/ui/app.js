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

/* ---------- session / roles ---------- */
const ROLES = { viewer: 0, editor: 1, admin: 2 };
let session = null;                       // { user, role }
const canEdit = () => session && ROLES[session.role] >= ROLES.editor;
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
  if (!res.ok) throw new Error(String(res.status));
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
    session = { user: me.user, role: me.role };
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
    session = { user: out.user, role: out.role };
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
  { id: "situations", label: "Situations", role: "viewer" },
  { id: "timeline", label: "Timeline", role: "viewer" },
  { id: "entities", label: "Entities", role: "viewer" },
  { id: "users", label: "Users", role: "admin" },
  { id: "tokens", label: "Tokens", role: "admin" },
  { id: "config", label: "Config", role: "admin" },
  { id: "quarantine", label: "Quarantine", role: "admin" },
  { id: "audit", label: "Audit", role: "admin" },
];
let activePanel = "situations";
function buildTabs() {
  const nav = $("tabs");
  clear(nav);
  for (const tab of TABS) {
    if (ROLES[session.role] < ROLES[tab.role]) continue;
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
  else if (id === "quarantine") loadQuarantine();
  else if (id === "audit") loadAudit();
}

// A.4: a role must not merely be unable to *reach* a screen it lacks — the screen is absent
// from its DOM entirely. Remove every panel whose role the caller does not hold. (Logout does a
// full reload, so a later higher-role login on the same page rebuilds the pruned panels.)
function prunePanels() {
  for (const tab of TABS) {
    if (ROLES[session.role] < ROLES[tab.role]) {
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
async function rename(kind, id, current) {
  const label = prompt(`Rename ${kind} (cosmetic label):`, current);
  if (label === null || !label.trim()) return;
  await api("/api/labels", { method: "POST", json: { kind, id, label: label.trim() } });
  poll();
}
async function feedback(sid, verdict) {
  await api(`/api/situations/${sid}/feedback`, { method: "POST", json: { verdict } });
  poll();
}
async function closeSituation(sid) {
  await api(`/api/situations/${sid}/close`, { method: "POST", json: {} });
  poll();
}
function alarmName(a) { return a.class_label || a.class_name || a.class_oid; }
function deviceName(a) { return a.device_label || a.device_ip; }

function termBar(l) {
  const part = (x) => Math.max(1, Math.round(120 * (x / 0.95)));
  const bar = el("span", { class: "bar", title: `t=${l.term_t.toFixed(2)} A=${l.term_a.toFixed(2)} E=${l.term_e.toFixed(2)}` });
  for (const [cls, val] of [["t", l.term_t], ["a", l.term_a], ["e", l.term_e]]) {
    const fill = el("i", { class: cls });
    fill.style.width = part(val) + "px";           // CSSOM (allowed by CSP), not inline attr
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
  clear(sits);
  if (!list.length) { sits.append(el("div", { class: "empty", text: "No situations match — the network is quiet." })); return; }
  for (const s of list) {
    const detail = el("div", { class: "detail" });
    detail.style.display = expanded.has(s.id) ? "block" : "none";
    const head = el("div", { class: "sit-head" },
      el("span", { class: "sid", text: "#" + s.id }),
      el("span", { class: "badge " + (s.status === "open" ? "alarm" : ""), text: s.status }),
      el("span", { class: "badge", text: `${s.alarm_count} alarm${s.alarm_count === 1 ? "" : "s"}` }),
      el("span", { class: "age", text: age(s.updated_at) }));
    head.addEventListener("click", async () => {
      if (expanded.has(s.id)) { expanded.delete(s.id); detail.style.display = "none"; return; }
      expanded.add(s.id); detail.style.display = "block"; await renderDetail(detail, s.id);
    });
    const card = el("div", { class: "sit" }, head, detail);
    sits.append(card);
    if (expanded.has(s.id)) renderDetail(detail, s.id);
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
