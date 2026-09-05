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
    const boxes = detail.querySelectorAll('input[type="checkbox"]');
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
    const boxes = detailBefore.querySelectorAll("input");
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
