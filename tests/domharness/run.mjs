/* The scenario driver: `node --experimental-vm-modules run.mjs <scenario> <params.json>`
 * -> one JSON object on stdout.
 *
 * Scenarios are written here, in JavaScript, rather than expressed as data the Python side
 * interprets. The gestures the invariants need — expand a card, tick two of five members, hold a
 * button reference across a server-sent event, click the button you were already holding,
 * navigate by fragment, walk the sidebar with the keyboard — are control flow, and encoding
 * control flow as JSON would add an interpreter with its own bugs between the test and the thing
 * under test.
 *
 * Every scenario returns `proof`, which is how the Python side knows the UI was really evaluated
 * rather than the harness having quietly done nothing. A run whose `proof.escaped` is not the
 * string the UI's own `esc()` produces is not counted as an execution.
 *
 * ## v0.13.0: the proof moved, and it got stronger
 *
 * v0.12.0 read `esc` off the sandbox's global object, because the UI was a classic script. An ES
 * module exports nothing to the global, so the proof now calls `esc` **on the evaluated module
 * namespace of `app/dom.js`** — the same module instance the running UI imported. That cannot be
 * produced without linking and evaluating the real graph, and it additionally proves the graph
 * linked at all. `proof.mounted` is the second half: the UI rendered something into `#root`.
 */

import fs from "node:fs";
import path from "node:path";
import { createEnvironment, evaluateModules, settle } from "./env.mjs";
import { runSelfTests } from "./selftest.mjs";

// An `async function` that throws REJECTS; it does not throw at the call site. Collecting them
// here keeps an unhandled rejection from taking the process down, and is itself an observation.
const rejections = [];
process.on("unhandledRejection", (reason) => { rejections.push(reason); });

const UI = path.join(import.meta.dirname, "..", "..", "src", "netcorenoc", "ui");
const DANGEROUS = new Set(["script", "img", "iframe", "object", "embed", "link", "style", "form"]);

async function boot(params) {
  const env = createEnvironment({
    indexHtml: fs.readFileSync(path.join(UI, "index.html"), "utf8"),
    routes: params.routes ?? {},
    dialogs: params.dialogs ?? {},
    cookies: params.cookies ?? {},
    hash: params.hash ?? "",
  });
  await evaluateModules(env, { uiDir: UI, entry: "app.js" });
  // `app.js` exports `booted`, the promise of its own boot sequence. Awaiting it rather than
  // racing it is what makes two runs identical: the alternative is to `settle()` enough times and
  // hope, which is exactly the kind of "probably finished" the determinism test would catch late.
  await env.entry.namespace.booted;
  await settle(env);
  return env;
}

/** Proof that the UI's own code ran in this context. */
function proofOf(env) {
  const domModule = [...env.modules.entries()].find(([file]) => file.endsWith("app/dom.js"));
  const namespace = domModule ? domModule[1].namespace : null;
  return {
    modulesEvaluated: env.modules.size,
    // `esc()` is the UI's own escaper. Running it is a fact about the UI, not about the harness.
    escaped: namespace && typeof namespace.esc === "function" ? namespace.esc('<a href="x">') : null,
    mounted: (env.document.getElementById("root")?.childNodes.length ?? 0) > 0,
  };
}

/** The structural facts every render scenario reports, so a test never re-derives them. */
const view = (env) => ({
  navItems: env.document.querySelectorAll("#nav a").map((a) => a.textContent.trim()),
  navHrefs: env.document.querySelectorAll("#nav a").map((a) => a.getAttribute("href")),
  navGroups: env.document.querySelectorAll("#nav h2").map((h) => h.textContent),
  activeView: env.document.querySelector(".view")?.dataset.view ?? null,
  refused: env.document.querySelectorAll(".state-refused").length > 0,
  unknown: env.document.querySelectorAll(".state-empty h3")
    .some((h) => h.textContent === "No such screen"),
  appVisible: env.document.querySelectorAll("#app").length > 0,
  loginVisible: env.document.querySelectorAll("#login").length > 0,
  theme: env.document.documentElement?.getAttribute("data-theme") ?? null,
  density: env.document.documentElement?.getAttribute("data-density") ?? null,
});

const requests = (env) =>
  env.network.requests.map((r) => ({ method: r.method, path: r.path, body: r.body }));

/** An indented structural dump of a subtree. The gate documents quote this. */
function dumpTree(node, depth = 0, out = []) {
  for (const child of node?.childNodes ?? []) {
    if (child.nodeType === 3) {
      const text = child.data.trim();
      if (text) out.push(`${"  ".repeat(depth)}"${text.slice(0, 78)}"`);
      continue;
    }
    if (child.nodeType !== 1) continue;
    const id = child.getAttribute("id");
    const cls = child.getAttribute("class");
    const role = child.getAttribute("role");
    const label = child.getAttribute("aria-label");
    let line = `${"  ".repeat(depth)}<${child.tagName}`;
    if (id) line += ` #${id}`;
    if (cls) line += ` .${cls.split(/\s+/).join(".")}`;
    if (role) line += ` role=${role}`;
    if (label) line += ` aria-label="${label.slice(0, 40)}"`;
    out.push(`${line}>`);
    dumpTree(child, depth + 1, out);
  }
  return out;
}

/** The card for a situation id, and its detail node. */
function cardFor(env, sid) {
  const card = env.document.querySelectorAll(".sit").find((c) => c.dataset.sid === String(sid));
  if (!card) throw new Error(`no card rendered for situation ${sid}`);
  return { card, toggle: card.querySelector(".sit-toggle"), detail: card.querySelector(".detail") };
}

// `trim()` since v0.15.3, and it is a correction rather than a loosening. A button whose label is
// preceded by an icon renders `" Confirm grouping"` — the leading space is markup whitespace no
// operator can see, and matching on it made the selector sensitive to how the template happened to
// wrap. What a scenario means by "the Confirm button" is the label, so that is what is compared.
function buttonIn(node, prefix) {
  const found = node.querySelectorAll("button").find((b) => b.textContent.trim().startsWith(prefix));
  if (!found) {
    throw new Error(
      `no button starting ${JSON.stringify(prefix)} in the rendered card ` +
      `(saw: ${node.querySelectorAll("button").map((b) => JSON.stringify(b.textContent)).join(", ")})`,
    );
  }
  return found;
}

/* ---------- scenarios ---------- */

const scenarios = {
  /** Boot and report what the role can see. Invariant 1 reads this. */
  async boot(params) {
    const env = await boot(params);
    return { ...view(env), requests: requests(env), proof: proofOf(env) };
  },

  /**
   * Boot, then push two `/api/stats` payloads through the live store and read the health tiles.
   *
   * v0.15.2 puts `queue_depth` and the five receiver counters on the Overview and derives a trap
   * rate **in the client** between two updates (DECISIONS #222). A rate derived from one sample is
   * a rate that is always zero, and a tile that always reads `0.00 /s` is indistinguishable from a
   * broken derivation — so this drives two, with a known gap, and reports what the screen says.
   */
  async health(params) {
    const env = await boot(params);
    const entry = [...env.modules.entries()].find(([file]) => file.endsWith("app/store.js"));
    const store = entry[1].namespace;
    const read = () => {
      const tiles = [...env.document.querySelectorAll(".stat")];
      const out = {};
      for (const tile of tiles) {
        const label = tile.querySelector(".stat-label");
        const value = tile.querySelector(".stat-value");
        const note = tile.querySelector(".stat-note");
        if (label && value) {
          out[label.textContent.trim()] = {
            value: value.textContent.trim(),
            note: note ? note.textContent.trim() : null,
          };
        }
      }
      return out;
    };
    env.navigate("#/overview");
    await settle(env);
    const samples = [];
    for (const stats of params.samples ?? []) {
      if (samples.length) env.advanceClock(params.advanceMs ?? 2500);
      store.applyUpdate({ stats });
      await settle(env);
      samples.push({ tiles: read(), rate: store.get().trapRate });
    }
    return { samples, proof: proofOf(env) };
  },

  /** Boot, optionally navigate, and dump the DOM. The gate documents quote `dump`. */
  async render(params) {
    const env = await boot(params);
    if (params.navigate) { env.navigate(params.navigate); await settle(env); }
    return {
      ...view(env),
      dump: dumpTree(env.document.getElementById("root")).join("\n"),
      requests: requests(env),
      requestPaths: env.network.requests.map((r) => `${r.method} ${r.path}`),
      proof: proofOf(env),
    };
  },

  /**
   * Navigate to each fragment in turn and report what happened — including whether a request was
   * issued. **This is the F53 scenario**: it is what a deep link or a bookmark does, and it is the
   * thing v0.12.0's UI had no defence against except a `TypeError`.
   */
  async navigateTo(params) {
    const env = await boot(params);
    const outcomes = {};
    for (const fragment of params.fragments ?? []) {
      const before = env.network.requests.length;
      let error = null;
      const seen = rejections.length;
      try { env.navigate(fragment); await settle(env); }
      catch (raised) { error = `${raised.constructor.name}: ${raised.message}`; }
      const raised = rejections.slice(seen);
      outcomes[fragment] = {
        activeView: env.document.querySelector(".view")?.dataset.view ?? null,
        refused: env.document.querySelectorAll(".state-refused").length > 0,
        unknown: env.document.querySelectorAll(".state-empty h3")
          .some((h) => h.textContent === "No such screen"),
        // The whole point: what did resolving this address ASK THE SERVER FOR?
        paths: env.network.requests.slice(before).map((r) => `${r.method} ${r.path}`),
        // v0.12.0's mechanism was an exception. If one appears here, the repair has regressed.
        threw: error ?? (raised.length ? `${raised[0].constructor.name}: ${raised[0].message}` : null),
        heading: env.document.querySelector(".work-heading h1")?.textContent ?? null,
      };
    }
    return { outcomes, requestPaths: env.network.requests.map((r) => `${r.method} ${r.path}`), proof: proofOf(env) };
  },

  /**
   * Expand a card and read "Why these were grouped" — before and after opening the detail.
   *
   * v0.15.3 (V.6, #245): the section used to render `links.slice(0, 30)` unconditionally. It now
   * renders a summary computed from EVERY link, with the per-link decomposition behind one
   * interaction and **complete** when opened. Both halves are measured here, because principle 2
   * — the per-term contributions are reachable — is what this screen exists for and truncation is
   * the way it silently stops being true.
   */
  async whyGrouped(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const why = () => cardFor(env, params.sid).detail.querySelector(".why");
    const rows = () => why().querySelectorAll(".linkrow");
    const read = () => ({
      rowCount: rows().length,
      // Scoped to `.linkrow`: the summary's per-term MEANS carry `.term-num` too (deliberately —
      // same rule, a bar is never alone), and counting both would conflate the two claims.
      termNumbers: why().querySelectorAll(".linkrow .term-num").map((n) => n.textContent.trim()),
      summaryText: (why().querySelector(".soundness")?.textContent ?? "")
        .replace(/\s+/g, " ").trim(),
      // `className`, not `classList`: this DOM's classList is not iterable, and reaching for one
      // that is would be testing the harness rather than the console.
      band: String(why().querySelector(".soundness")?.className ?? "")
        .split(/\s+/).find((c) => c.startsWith("soundness-")) ?? null,
      means: why().querySelectorAll(".term-mean-label").map((n) => n.textContent.trim()),
    });

    const closed = read();
    const toggle = buttonIn(why(), "Show");
    toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const opened = read();

    return {
      closed,
      opened,
      toggleLabel: toggle.textContent.replace(/\s+/g, " ").trim(),
      expandedAttr: why().querySelector(".link-detail-toggle").getAttribute("aria-expanded"),
      proof: proofOf(env),
    };
  },

  /**
   * Expand a card, tick the members named by `mark` (indices into the alarm list), click Split.
   * Invariant 2 reads `feedbackBody`.
   */
  async partialSplit(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const detail = cardFor(env, params.sid).detail;
    // v0.16.0: `input` alone no longer means "a member checkbox". The card also carries the
    // confidence range, two situation-id number fields and the name text field, so the selector
    // says which inputs it means rather than relying on the card having only one kind.
    //
    // v0.16.4: **and it says which part of the table it means.** The mark column's header now
    // carries a select-all, so `input[type="checkbox"]` inside the card includes one control that
    // is not a member — and taking it as index 0 shifted every mark by one row, which is exactly
    // the shape invariant 2 exists to refuse. `tbody` is the rows.
    const boxes = detail.querySelectorAll('tbody input[type="checkbox"]');
    for (const index of params.mark ?? []) {
      const box = boxes[index];
      if (!box) throw new Error(`no member checkbox at index ${index} (${boxes.length} rendered)`);
      box.checked = true;
      box.dispatchEvent(new env.DomEvent("change"));
      await settle(env);
    }
    buttonIn(cardFor(env, params.sid).detail, params.button ?? "Split")
      .dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const post = env.network.requests.find((r) => r.method === "POST" && r.path.includes("/feedback"));
    return {
      checkboxCount: boxes.length,
      feedbackPath: post?.path ?? null,
      feedbackBody: post?.body ?? null,
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * Expand a card and read back every severity pill the members table rendered.
   *
   * v0.16.2. The accessibility rule `format.js` has documented since v0.13.0 is that a severity
   * carries a colour AND a glyph AND its text; this is what makes it checkable at the DOM rather
   * than in prose. The caller doctors the captured payload so every band is on screen at once —
   * a real corpus resolves no severity at all, which is a fact about the corpus and not a reason
   * to leave four of the five bands unrendered by any test.
   */
  async severityBands(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const cells = [...cardFor(env, params.sid).detail.querySelectorAll("td.sev")];
    return {
      cells: cells.map((td) => {
        const pill = td.querySelector(".sev-pill");
        return {
          classes: pill ? (pill.getAttribute("class") ?? "") : "",
          glyph: pill ? (pill.querySelector(".sev-glyph")?.textContent ?? "") : "",
          text: pill ? (pill.querySelector(".sev-text")?.textContent ?? "") : "",
          // The whole cell's text, so a pill that dropped its word but kept a `title=` is not
          // mistaken for one that renders it: a tooltip is not a rendering.
          cellText: td.textContent.replace(/\s+/g, " ").trim(),
        };
      }),
      proof: proofOf(env),
    };
  },

  /**
   * Follow a sequence of situation permalinks and report which card is open after each (F108).
   *
   * v0.16.4. A hash change **inside** the Situations screen is a same-document navigation, so the
   * component is not remounted and `componentDidMount`'s deep link is never read again. Measured
   * in a browser: the address bar said `#/situations/41` while the card for 38 was the one still
   * open. This drives the address the way the router does rather than the way a page load does,
   * which is the only difference between the case that worked and the case that did not.
   */
  async permalink(params) {
    const env = await boot(params);
    const steps = [];
    for (const fragment of params.fragments ?? []) {
      env.navigate(fragment);
      await settle(env);
      steps.push({
        fragment,
        hash: env.location?.hash ?? null,
        expanded: env.document.querySelectorAll(".sit.expanded").map((c) => c.dataset.sid),
        cards: env.document.querySelectorAll(".sit").map((c) => c.dataset.sid),
      });
    }
    return { steps, proof: proofOf(env) };
  },

  /**
   * The top bar's two disclosures, and the sidebar's two states (v0.16.4, #288-#290).
   *
   * Opens each panel, reads what it holds, closes it with Escape, and toggles the sidebar — all
   * through the events a browser raises, because every one of these is a control whose *state* is
   * the thing under test and none of them is visible in the markup at rest.
   */
  async shellControls(params) {
    const env = await boot(params);
    const q = (sel) => env.document.querySelector(sel);
    const read = (id) => {
      const panel = env.document.getElementById(id);
      return {
        open: panel ? !panel.hasAttribute("hidden") : null,
        label: panel?.getAttribute("aria-label") ?? null,
        text: panel?.textContent.replace(/\s+/g, " ").trim() ?? null,
        links: (panel?.querySelectorAll("a") ?? []).map((a) => a.getAttribute("href")),
        items: (panel?.querySelectorAll(".notice-text") ?? [])
          .map((n) => n.textContent.replace(/\s+/g, " ").trim()),
        // The health panel's row LABELS: what it claims to measure, as distinct from the prose
        // beside them saying what it cannot. v0.16.5 turned the `<dl>` into `.meter` blocks, so
        // the label moved from a `<dt>` to `.meter-name`; both are read, because the correlation
        // counters kept their old shape in the secondary line.
        figures: [
          ...(panel?.querySelectorAll("dt") ?? []),
          ...(panel?.querySelectorAll(".meter-name") ?? []),
        ].map((d) => d.textContent.trim()),
        // What each meter actually renders: the percentage as shown and the detail beside it. A
        // metric the host will not give up must read `—` here and never `0%` (DECISIONS #289).
        meters: (panel?.querySelectorAll(".meter") ?? []).map((m) => ({
          name: m.querySelector(".meter-name")?.textContent.trim() ?? null,
          pct: m.querySelector(".meter-pct")?.textContent.trim() ?? null,
          detail: m.querySelector(".meter-detail")?.textContent.trim() ?? null,
          bar: m.querySelector(".meter-fill")?.getAttribute("style") ?? null,
          aria: m.querySelector(".meter-bar")?.getAttribute("aria-label") ?? null,
          // One polyline per unbroken run of readings: a gap must SPLIT the line rather than be
          // drawn through, so the count is the assertion and not just the presence of an svg.
          runs: m.querySelectorAll("polyline").length,
        })),
        // The dismiss v0.16.5 added. Escape, a second press and a click outside all worked before
        // and none of them was visible.
        closers: (panel?.querySelectorAll(".disclosure-close") ?? [])
          .map((b) => b.getAttribute("aria-label")),
      };
    };
    const press = async (sel) => {
      const node = q(sel);
      if (!node) throw new Error(`no control matching ${sel}`);
      node.dispatchEvent(new env.DomEvent("click"));
      await settle(env);
    };

    const out = { chips: env.document.querySelectorAll(".chip").length };
    out.bellClosed = read("noticePanel");
    await press('button[aria-controls="noticePanel"]');
    out.bellOpen = read("noticePanel");
    // A second press closes it. Escape does too — `notices.js` registers a document `keydown`
    // listener for it — and that path is driven in the browser rather than here, because this
    // harness's document has no key-event dispatch and a test that pretended otherwise would be
    // measuring the harness.
    await press('button[aria-controls="noticePanel"]');
    out.bellReclosed = read("noticePanel");

    await press('button[aria-controls="healthPanel"]');
    out.healthOpen = read("healthPanel");
    // Opening the health control is a click OUTSIDE the bell, so the bell must have closed with
    // it: two panels stacked on one another is a state neither of them can be dismissed from.
    out.bellAfterHealth = read("noticePanel");

    // Following a warning's link closes the panel. Found in the live pass: the click is *inside*
    // the disclosure, so the outside-click rule kept it open and it hung over the screen it had
    // just navigated to.
    await press('button[aria-controls="noticePanel"]');
    const link = env.document.querySelector("#noticePanel a[href]");
    if (link) {
      link.dispatchEvent(new env.DomEvent("click"));
      await settle(env);
      out.bellAfterLink = read("noticePanel");
      out.linkHref = link.getAttribute("href");
    }

    const navItems = () => env.document.querySelectorAll(".nav-item").map((a) => {
      const label = a.querySelector(".nav-label");
      return {
        label: a.getAttribute("aria-label"),
        text: label?.textContent ?? null,
        // HOW the label is hidden, which is the whole difference between a screen-reader operator
        // being able to use a collapsed rail and not. `.visually-hidden` clips; `display: none`
        // removes it from the tree, and no test here has a layout engine to tell them apart —
        // so the technique is a class in the DOM rather than a rule in a stylesheet.
        clipped: (label?.getAttribute("class") ?? "").split(/\s+/).includes("visually-hidden"),
      };
    });
    out.navExpanded = { items: navItems(), shell: q("#app")?.getAttribute("class") };
    await press("button.nav-toggle");
    out.navCollapsed = {
      items: navItems(),
      shell: q("#app")?.getAttribute("class"),
      cookie: String(env.document.cookie ?? ""),
      expandedAttr: q("button.nav-toggle")?.getAttribute("aria-expanded"),
    };
    await press("button.nav-toggle");
    out.navReexpanded = { shell: q("#app")?.getAttribute("class") };
    return { ...out, proof: proofOf(env) };
  },

  /**
   * Expand a card and report **what an operator can do on it** (v0.16.4, DECISIONS #291).
   *
   * Not what it displays: which controls are on the page, whether the judged disclosure is closed
   * over them, and — when `params.adjust` — the same list again after it is opened. The caller
   * doctors `events` to put the situation in each of the states the decision names, because a
   * corpus replay makes no gestures and would leave three of the four unreachable.
   */
  async actionSurface(params) {
    // Reached by its PERMALINK rather than by the list plus a click: this screen opens on the New
    // tab (DECISIONS #254), so a situation in any other state is not in the list to click on, and
    // the permalink pins it there (F97's repair, doing exactly the job it was built for).
    const env = await boot({ ...params, hash: `#/situations/${params.sid}` });
    await settle(env);
    const read = () => {
      const detail = cardFor(env, params.sid).detail;
      return {
        judged: detail.querySelector(".judged-note")?.textContent.replace(/\s+/g, " ").trim() ?? null,
        adjust: Boolean(detail.querySelector(".judged button")),
        grouping: detail.querySelectorAll(".fb button").map((b) => b.textContent.trim()),
        restructure: Boolean(detail.querySelector(".lifecycle")),
        nameField: Boolean(detail.querySelector("#lcName")),
        marks: detail.querySelectorAll('tbody input[type="checkbox"]').length,
        selectAll: Boolean(detail.querySelector('thead input[type="checkbox"]')),
        clears: detail.querySelectorAll("button.row-clear").length,
        declares: detail.querySelectorAll(".row-actions .declare-open").length,
      };
    };
    const before = read();
    let after = null;
    if (params.adjust && before.adjust) {
      cardFor(env, params.sid).detail.querySelector(".judged button")
        .dispatchEvent(new env.DomEvent("click"));
      await settle(env);
      after = read();
    }
    return { before, after, proof: proofOf(env) };
  },

  /**
   * Tick the mark column's header and report what the split then sends (v0.16.4).
   *
   * One corpus situation holds 1 051 members; a partial split over it is not a gesture anybody
   * completes one checkbox at a time. The assertion that matters is not that boxes appear ticked
   * — it is that the ids in the request are **exactly** the membership, which is invariant 2's
   * contract reached through a new control.
   */
  async markAll(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const header = cardFor(env, params.sid).detail.querySelector('thead input[type="checkbox"]');
    if (!header) throw new Error("the mark column's header carries no select-all");
    header.checked = true;
    header.dispatchEvent(new env.DomEvent("change"));
    await settle(env);
    const ticked = cardFor(env, params.sid).detail
      .querySelectorAll('tbody input[type="checkbox"]').filter((b) => b.checked).length;

    buttonIn(cardFor(env, params.sid).detail, "Split").dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const post = env.network.requests.find((r) => r.method === "POST" && r.path.includes("/feedback"));

    // …and untick, because a control that only goes one way is half a control.
    const header2 = cardFor(env, params.sid).detail.querySelector('thead input[type="checkbox"]');
    header2.checked = false;
    header2.dispatchEvent(new env.DomEvent("change"));
    await settle(env);
    const afterUntick = cardFor(env, params.sid).detail
      .querySelectorAll('tbody input[type="checkbox"]').filter((b) => b.checked).length;

    return {
      ticked,
      afterUntick,
      feedbackBody: post?.body ?? null,
      proof: proofOf(env),
    };
  },

  /**
   * Expand a card and read the gesture history **as text** (v0.16.4, Bug 2).
   *
   * The maintainer reported that `admin2` became `admin3`. Nobody renamed anything: the row
   * rendered `by admin` and the age with nothing between them, so `admin` + `2m` read as a name
   * with a counter after it. The layout half of the repair is a flex context this harness has no
   * way to see — it has no layout engine — and the **text** half is exactly what it can see, which
   * is why the repair has two halves and this scenario asserts one of them.
   *
   * The caller doctors the captured detail payload to carry events, for the reason
   * `severityBands` doctors severities: a fresh corpus replay makes no gestures, so a history
   * panel is unreachable on real data and would be untested by anything.
   */
  async history(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const list = cardFor(env, params.sid).detail.querySelector(".history-list");
    return {
      present: Boolean(list),
      // Raw, NOT whitespace-collapsed: whether the runs are separated at all is the whole
      // question, and `replace(/\s+/g, " ")` would erase the difference being measured.
      lines: (list?.querySelectorAll("li") ?? []).map((li) => li.textContent),
      ages: (list?.querySelectorAll(".age") ?? []).map((s) => s.textContent),
      proof: proofOf(env),
    };
  },

  /**
   * Make one declaration from a member row, and report what the click actually sent.
   *
   * v0.16.3. `params.control` is `"ne"`, `"class"` or `"severity"`; `params.row` is the member
   * index; `params.value` is what the operator types or selects. The scenario opens the editor,
   * sets the value, presses Save — and, when the disagreement warning appears, either presses the
   * button again (`params.anyway`) or presses Cancel.
   *
   * **Why the warning is driven here and not asserted in prose**: it is an element on the page
   * precisely so this is possible. A `globalThis.confirm()` would be invisible to this harness,
   * which is how eight consecutive releases shipped a console defect no test could see.
   */
  async declare(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const column = { ne: 0, class: 1, severity: 2 };
    const rowNode = cardFor(env, params.sid).detail.querySelectorAll("tbody tr")[params.row ?? 0];
    if (!rowNode) throw new Error(`no member row at index ${params.row ?? 0}`);
    const openers = rowNode.querySelectorAll("button.declare-open");
    const opener = openers[column[params.control]];
    if (!opener) {
      throw new Error(
        `no declaration control for ${params.control} (saw ${openers.length} on the row)`,
      );
    }
    const openerLabel = opener.textContent.trim();
    opener.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const form = rowNode.querySelector("form.declare");
    if (!form) throw new Error("the declaration editor did not open");
    // The harness DOM takes one simple selector at a time — deliberately, so an unrecognised
    // one cannot match nothing quietly. Two queries rather than a comma group.
    const field = form.querySelector("input") ?? form.querySelector("select");
    field.value = params.value;
    field.dispatchEvent(new env.DomEvent("input"));
    field.dispatchEvent(new env.DomEvent("change"));
    await settle(env);

    // The editor is a `<form onSubmit=…>` and its Save is `type="submit"`, so the gesture is a
    // submit rather than a click: this DOM does not synthesise one from the other, deliberately —
    // an implicit submit is browser behaviour and pretending to have it would be a fiction the
    // harness told the test. The button's label is still read, because it is what the operator
    // sees change when the confirmation appears.
    const saveLabel = buttonIn(form, "Save").textContent.trim();
    form.dispatchEvent(new env.DomEvent("submit"));
    await settle(env);

    // The warning, if it appeared. `warned` is what the interruption rule is measured by.
    const warnNode = rowNode.querySelector(".declare-warn");
    const warned = Boolean(warnNode);
    let confirmLabel = null;
    if (warned) {
      const live = rowNode.querySelector("form.declare");
      confirmLabel = buttonIn(live, "Declare anyway").textContent.trim();
      if (params.anyway) {
        live.dispatchEvent(new env.DomEvent("submit"));
      } else {
        buttonIn(live, "Cancel").dispatchEvent(new env.DomEvent("click"));
      }
      await settle(env);
    }

    const posts = env.network.requests.filter(
      (r) => r.method === "POST" && r.path === "/api/labels",
    );
    const deletes = env.network.requests.filter(
      (r) => r.method === "DELETE" && r.path.startsWith("/api/labels/"),
    );
    return {
      openerLabel,
      saveLabel,
      confirmLabel,
      warned,
      warnText: warnNode ? warnNode.textContent.replace(/\s+/g, " ").trim() : null,
      posts: posts.map((r) => r.body),
      deletePaths: deletes.map((r) => r.path),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * Withdraw a declaration that is already in force: open the editor and press `Clear`.
   *
   * **A declaration that cannot be undone is a declaration nobody will make**, so the revert is
   * driven rather than described.
   */
  async withdraw(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const column = { ne: 0, class: 1, severity: 2 };
    const rowNode = cardFor(env, params.sid).detail.querySelectorAll("tbody tr")[params.row ?? 0];
    const opener = rowNode.querySelectorAll("button.declare-open")[column[params.control]];
    const openerLabel = opener.textContent.trim();
    opener.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    const form = rowNode.querySelector("form.declare");
    buttonIn(form, "Clear").dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    return {
      openerLabel,
      deletePaths: env.network.requests
        .filter((r) => r.method === "DELETE")
        .map((r) => r.path),
      posts: env.network.requests.filter((r) => r.method === "POST").map((r) => r.path),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * The v0.7.5 defect, re-created as an observation: hold the Split button the operator is
   * aiming at, let a server-sent update arrive, and only then click it.
   */
  async sseDuringGesture(params) {
    const env = await boot(params);
    env.navigate("#/situations");
    await settle(env);
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const detailBefore = cardFor(env, params.sid).detail;
    const splitBefore = buttonIn(detailBefore, "Split");
    // v0.16.4: `tbody`, and the same correction as `partialSplit` above — the mark column's
    // header now carries a select-all, so a card-wide `input` scan takes a control that is not a
    // member as index 0 and every mark lands one row late.
    const boxes = detailBefore.querySelectorAll('tbody input[type="checkbox"]');
    for (const index of params.mark ?? []) {
      boxes[index].checked = true;
      boxes[index].dispatchEvent(new env.DomEvent("change"));
      await settle(env);
    }

    // The update lands mid-gesture: the operator has ticked boxes but not yet clicked.
    const source = env.eventSources[0];
    if (!source) throw new Error("the UI opened no EventSource; the SSE path is not under test");
    source.emit("update", params.update);
    await settle(env);

    const detailAfter = cardFor(env, params.sid).detail;
    const stillConnected = splitBefore.isConnected;
    splitBefore.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const post = env.network.requests.find((r) => r.method === "POST" && r.path.includes("/feedback"));
    return {
      sameDetailNode: detailBefore === detailAfter,
      buttonStillConnected: stillConnected,
      detailStillConnected: detailAfter.isConnected,
      heldMarkerPresent: cardFor(env, params.sid).card.textContent.includes("held while open"),
      feedbackPath: post?.path ?? null,
      feedbackBody: post?.body ?? null,
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /** Drive hostile strings through every render path a card reaches. Invariant 4 reads this. */
  async hostilePayload(params) {
    const hostile = params.hostile;
    if (!hostile) throw new Error("hostilePayload needs the `hostile` string the fixture labelled with");
    const env = await boot(params);
    const baseline = census(env.document);

    /* Census after EVERY step and accumulate.
     *
     * v0.12.0's panels were shown and hidden, so a payload rendered on one stayed in the document
     * while the next was opened and a single census at the end saw everything. v0.13.0 UNMOUNTS
     * the screen it leaves, so the same census reported zero — not because the payload was safe
     * but because it was gone. That zero would have satisfied every "no dangerous element"
     * assertion in the file while the control that catches exactly this ("did the payload reach
     * the DOM at all?") was the only thing that failed. It did fail, which is why this is a sum.
     */
    let textHits = 0;
    let attrHits = 0;
    const introduced = {};
    const tagsSeen = new Set();
    const record = () => {
      const now = census(env.document);
      textHits += now.texts.filter((t) => t.includes(hostile)).length;
      attrHits += now.attrs.filter((a) => a.includes(hostile)).length;
      for (const [tag, count] of Object.entries(now.tags)) {
        tagsSeen.add(tag);
        const delta = count - (baseline.tags[tag] ?? 0);
        if (delta > 0 && DANGEROUS.has(tag)) {
          introduced[tag] = Math.max(introduced[tag] ?? 0, delta);
        }
      }
    };

    env.navigate("#/situations");
    await settle(env);
    record();
    cardFor(env, params.sid).toggle.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    record();
    env.navigate("#/entities");
    await settle(env);
    record();
    env.navigate("#/classes");
    await settle(env);
    record();

    return {
      hostile,
      dangerousElementsIntroduced: introduced,
      payloadInTextNodes: textHits,
      payloadInAttributeValues: attrHits,
      elementTagsAfter: [...tagsSeen].sort(),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * Boot at a capability set, visit every view the UI offers, and report every request.
   * Invariant 5 reads this; its control is the same scenario at admin.
   */
  async capabilityRequests(params) {
    const env = await boot(params);
    const bootRequests = env.network.requests.map((r) => `${r.method} ${r.path}`);
    const offered = env.document.querySelectorAll("#nav a").map((a) => a.getAttribute("href"));
    const perView = {};
    for (const viewId of params.visit ?? []) {
      const before = env.network.requests.length;
      env.navigate(`#/${viewId}`);
      await settle(env);
      perView[viewId] = {
        // "the view was not offered in navigation" is the finding, not an error.
        offered: offered.includes(`#/${viewId}`),
        refused: env.document.querySelectorAll(".state-refused").length > 0,
        activeView: env.document.querySelector(".view")?.dataset.view ?? null,
        paths: env.network.requests.slice(before).map((r) => `${r.method} ${r.path}`),
      };
    }
    return {
      ...view(env),
      viewsOffered: offered,
      bootRequests,
      perView,
      requestPaths: env.network.requests.map((r) => `${r.method} ${r.path}`),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /** Walk the sidebar with the keyboard alone, and report where focus went. */
  async keyboard(params) {
    const env = await boot(params);
    const nav = env.document.getElementById("nav");
    const items = () => env.document.querySelectorAll("#nav a");
    const tabIndexes = () => items().map((a) => a.getAttribute("tabindex"));

    const trace = [{ step: "initial", tabindex: tabIndexes() }];
    for (const key of params.keys ?? []) {
      const focused = items().find((a) => a.getAttribute("tabindex") === "0") ?? items()[0];
      focused.dispatchEvent(new env.DomEvent("keydown", { key, bubbles: true }));
      await settle(env);
      trace.push({
        step: key,
        tabindex: tabIndexes(),
        at: items().findIndex((a) => a.getAttribute("tabindex") === "0"),
        activeView: env.document.querySelector(".view")?.dataset.view ?? null,
      });
    }
    return {
      // Exactly one tab stop in the whole navigation: roving tabindex, not fourteen stops.
      tabStops: tabIndexes().filter((t) => t === "0").length,
      itemCount: items().length,
      navLabel: nav.getAttribute("aria-label"),
      headingFocusable: env.document.querySelector(".work-heading h1")?.getAttribute("tabindex") ?? null,
      trace,
      proof: proofOf(env),
    };
  },

  /** Drive a form and report exactly what the client sent — or that it sent nothing. */
  async submitForm(params) {
    const env = await boot(params);
    env.navigate(params.navigate);
    await settle(env);
    const before = env.network.requests.length;

    for (const [selector, value] of Object.entries(params.fields ?? {})) {
      const field = env.document.querySelector(selector);
      if (!field) throw new Error(`no field matched ${JSON.stringify(selector)}`);
      field.value = String(value);
      field.dispatchEvent(new env.DomEvent("input", { bubbles: true, target: field }));
      await settle(env);
    }
    for (const label of params.click ?? []) {
      const button = env.document.querySelectorAll("button")
        .find((b) => b.textContent.trim().startsWith(label));
      if (!button) {
        throw new Error(
          `no button starting ${JSON.stringify(label)}; saw ` +
          env.document.querySelectorAll("button").map((b) => JSON.stringify(b.textContent.trim())).join(", "),
        );
      }
      button.dispatchEvent(new env.DomEvent("click"));
      await settle(env);
    }

    return {
      sent: env.network.requests.slice(before).map((r) => ({ method: r.method, path: r.path, body: r.body })),
      refusalShown: env.document.querySelectorAll(".refusal").length > 0,
      refusalText: env.document.querySelector(".refusal")?.textContent ?? null,
      consequenceShown: env.document.querySelectorAll(".consequence").length > 0,
      dump: dumpTree(env.document.querySelector(".view")).join("\n"),
      dialogs: env.dialogCalls,
      proof: proofOf(env),
    };
  },

  /** Boot with a cookie, toggle the theme, and report what was written back.
   *
   * `trail` records the state AND the control's own label after every click, not just at the end
   * (F87). Reporting only the final state is what let two defects hide here: a click that changed
   * nothing on screen, and a label frozen at whatever it said on first render. Neither is visible
   * unless something looks between the clicks. */
  async theme(params) {
    const env = await boot(params);
    const label = (b) => (b && b.getAttribute("aria-label")) ?? null;
    const control = () => env.document.querySelectorAll("button")
      .find((b) => (b.getAttribute("aria-label") ?? "").startsWith("Theme:")) ?? null;
    const snapshot = () => ({
      theme: env.document.documentElement.getAttribute("data-theme"),
      density: env.document.documentElement.getAttribute("data-density"),
      cookie: env.cookies.value,
      control: label(control()),
    });

    const before = snapshot();
    const trail = [before];
    for (const wanted of params.click ?? []) {
      const button = env.document.querySelectorAll("button")
        .find((b) => (b.getAttribute("aria-label") ?? "").startsWith(wanted));
      if (!button) throw new Error(`no control whose aria-label starts ${JSON.stringify(wanted)}`);
      button.dispatchEvent(new env.DomEvent("click"));
      await settle(env);
      trail.push(snapshot());
    }
    return {
      before,
      after: snapshot(),
      trail,
      cookiePairs: Object.fromEntries(env.cookies.raw),
      proof: proofOf(env),
    };
  },

  /** The instrument's own conformance suite. Nothing about the UI. */
  async selfTest() {
    return runSelfTests();
  },

  /** Report the runtime the harness detected, for the gate evidence. */
  async environment() {
    return { node: process.version, platform: process.platform, tz: process.env.TZ ?? null };
  },
};

function census(document) {
  const tags = {};
  const texts = [];
  const attrs = [];
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 1) {
        tags[child.tagName] = (tags[child.tagName] ?? 0) + 1;
        for (const { value } of child.attributes) attrs.push(value);
        walk(child);
      } else if (child.nodeType === 3) {
        texts.push(child.data);
      }
    }
  };
  walk(document);
  return { tags, texts, attrs };
}

/* ---------- entry point ---------- */

const [, , name, paramsPath] = process.argv;
if (!Object.hasOwn(scenarios, name)) {
  process.stdout.write(JSON.stringify({ error: `unknown scenario ${name}`, known: Object.keys(scenarios) }));
  process.exit(2);
}
const params = paramsPath ? JSON.parse(fs.readFileSync(paramsPath, "utf8")) : {};
try {
  process.stdout.write(JSON.stringify(await scenarios[name](params)));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error && error.stack ? error.stack : error) }));
  process.exit(1);
}
