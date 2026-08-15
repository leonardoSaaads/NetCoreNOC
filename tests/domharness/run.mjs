/* The scenario driver: `node run.mjs <scenario> <params.json>` -> one JSON object on stdout.
 *
 * Scenarios are written here, in JavaScript, rather than expressed as data the Python side
 * interprets. The gestures the invariants need — expand a card, tick two of five members, hold a
 * button reference across a server-sent event, click the button you were already holding — are
 * control flow, and encoding control flow as JSON would add an interpreter with its own bugs
 * between the test and the thing under test.
 *
 * Every scenario returns `proof`, which is how the Python side knows `ui/app.js` was really
 * evaluated rather than the harness having quietly done nothing. A run whose `proof.escaped` is
 * not the string `app.js` itself produces is not counted as an execution.
 */

import fs from "node:fs";
import path from "node:path";
import { createEnvironment, settle } from "./env.mjs";
import { runSelfTests } from "./selftest.mjs";

// An `async function` that throws REJECTS; it does not throw at the call site. `renderPanel` calls
// its loaders without awaiting them, so a loader that dies takes the process down as an unhandled
// rejection unless they are collected here. Collecting them is also the observation
// `panelWithoutCapability` reports.
const rejections = [];
process.on("unhandledRejection", (reason) => { rejections.push(reason); });

const UI = path.join(import.meta.dirname, "..", "..", "src", "netcorenoc", "ui");
const DANGEROUS = new Set(["script", "img", "iframe", "object", "embed", "link", "style", "form"]);

function boot(params) {
  return createEnvironment({
    indexHtml: fs.readFileSync(path.join(UI, "index.html"), "utf8"),
    appJs: fs.readFileSync(path.join(UI, "app.js"), "utf8"),
    routes: params.routes ?? {},
    dialogs: params.dialogs ?? {},
  });
}

/** Proof that `app.js`'s own code ran in this context and is callable. */
function proofOf(env) {
  const { context } = env;
  const defined = ["esc", "el", "renderSituations", "applyUpdate", "prunePanels", "enterApp"]
    .filter((name) => typeof context[name] === "function");
  return {
    functionsDefined: defined.length,
    functionNames: defined,
    // `esc()` is app.js's own escaper. Running it is a fact about app.js, not about the harness.
    escaped: typeof context.esc === "function" ? context.esc('<a href="x">') : null,
  };
}

const view = (env) => ({
  tabs: env.document.getElementById("tabs")?.children.map((b) => b.textContent) ?? [],
  panels: env.document.querySelectorAll(".panel").map((p) => p.dataset.panel),
  appVisible: !env.document.getElementById("app").classList.contains("hidden"),
  loginVisible: !env.document.getElementById("login").classList.contains("hidden"),
});

const requests = (env) => env.network.requests.map((r) => ({ method: r.method, path: r.path, body: r.body }));

/** Click the tab whose label matches, returning whether it existed. */
function clickTab(env, label) {
  const tab = env.document.getElementById("tabs").children.find((b) => b.textContent === label);
  if (!tab) return false;
  tab.dispatchEvent(new env.DomEvent("click"));
  return true;
}

/** The card for a situation id, and its detail node (the second child, per renderSituations). */
function cardFor(env, sid) {
  const card = env.document.getElementById("sits").children.find((c) => c.dataset.sid === String(sid));
  if (!card) throw new Error(`no card rendered for situation ${sid}`);
  return { card, head: card.firstChild, detail: card.lastChild };
}

function buttonIn(node, prefix) {
  const found = node.querySelectorAll("button").find((b) => b.textContent.startsWith(prefix));
  if (!found) throw new Error(`no button starting ${JSON.stringify(prefix)} in the rendered card`);
  return found;
}

/* ---------- scenarios ---------- */

const scenarios = {
  /** Boot and report what the role can see. Invariant 1 reads this. */
  async boot(params) {
    const env = boot(params);
    await settle(env);
    return { ...view(env), requests: requests(env), proof: proofOf(env) };
  },

  /**
   * Expand a card, tick the members named by `mark` (indices into the alarm list), click Split.
   * Invariant 2 reads `feedbackBody`.
   */
  async partialSplit(params) {
    const env = boot(params);
    await settle(env);
    const { detail } = cardFor(env, params.sid);
    cardFor(env, params.sid).head.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const boxes = detail.querySelectorAll("input");
    for (const index of params.mark ?? []) {
      const box = boxes[index];
      if (!box) throw new Error(`no member checkbox at index ${index} (${boxes.length} rendered)`);
      box.checked = true;
      box.dispatchEvent(new env.DomEvent("change"));
    }
    buttonIn(detail, params.button ?? "✗ Split").dispatchEvent(new env.DomEvent("click"));
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
   * The v0.7.5 defect, re-created as an observation: hold the Split button the operator is
   * aiming at, let a server-sent update arrive, and only then click it.
   */
  async sseDuringGesture(params) {
    const env = boot(params);
    await settle(env);
    cardFor(env, params.sid).head.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const detailBefore = cardFor(env, params.sid).detail;
    const splitBefore = buttonIn(detailBefore, "✗ Split");
    const boxes = detailBefore.querySelectorAll("input");
    for (const index of params.mark ?? []) {
      boxes[index].checked = true;
      boxes[index].dispatchEvent(new env.DomEvent("change"));
    }

    // The update lands mid-gesture: the operator has ticked boxes but not yet clicked.
    const source = env.eventSources[0];
    if (!source) throw new Error("app.js opened no EventSource; the SSE path is not under test");
    source.emit("update", params.update);
    await settle(env);

    const detailAfter = cardFor(env, params.sid).detail;
    const stillConnected = splitBefore.isConnected;
    splitBefore.dispatchEvent(new env.DomEvent("click"));
    await settle(env);

    const post = env.network.requests.find((r) => r.method === "POST" && r.path.includes("/feedback"));
    return {
      // The three facts FEEDBACK-PATH-0.7.5-DRAFT §5 asked for and could not check.
      sameDetailNode: detailBefore === detailAfter,
      buttonStillConnected: stillConnected,
      detailStillConnected: detailAfter.isConnected,
      heldMarkerPresent: cardFor(env, params.sid).head.textContent.includes("held while open"),
      feedbackPath: post?.path ?? null,
      feedbackBody: post?.body ?? null,
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /** Drive hostile strings through every render path a card reaches. Invariant 4 reads this. */
  async hostilePayload(params) {
    // The payload is supplied by the caller rather than duplicated here. A copy in this file
    // drifted from `uifixtures.HOSTILE` on the first attempt and the scenario silently found
    // nothing; only the "did the payload reach the DOM at all" control noticed.
    const hostile = params.hostile;
    if (!hostile) throw new Error("hostilePayload needs the `hostile` string the fixture labelled with");
    const env = boot(params);
    await settle(env);
    const baseline = census(env.document);
    cardFor(env, params.sid).head.dispatchEvent(new env.DomEvent("click"));
    await settle(env);
    clickTab(env, "Entities");
    await settle(env);
    const after = census(env.document);

    const introduced = {};
    for (const [tag, count] of Object.entries(after.tags)) {
      const delta = count - (baseline.tags[tag] ?? 0);
      if (delta > 0 && DANGEROUS.has(tag)) introduced[tag] = delta;
    }
    return {
      hostile,
      dangerousElementsIntroduced: introduced,
      // Where the payload actually landed. `asText` is the safe outcome; `asMarkup` is the defect.
      payloadInTextNodes: after.texts.filter((t) => t.includes(hostile)).length,
      payloadInAttributeValues: after.attrs.filter((a) => a.includes(hostile)).length,
      elementTagsAfter: Object.keys(after.tags).sort(),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * Boot at a capability set, then perform every gesture the rendered UI offers, and report
   * every request. Invariant 5 reads this; its control is the same scenario at admin.
   */
  async capabilityRequests(params) {
    const env = boot(params);
    await settle(env);
    const bootRequests = env.network.requests.map((r) => `${r.method} ${r.path}`);
    // Attribute requests to the tab that caused them. This is what replaces `split("const TABS")`:
    // the panel -> capability mapping is DISCOVERED BY EXECUTION and then checked against
    // `rbac.ROUTE_PERMISSIONS`, instead of being read out of the source text of the map it is
    // supposed to be checking.
    const perTab = {};
    const offered = env.document.getElementById("tabs").children.map((b) => b.textContent);
    for (const label of params.clickTabs ?? []) {
      const before = env.network.requests.length;
      // Report rather than throw: "the tab was not offered" is the finding, not an error.
      const existed = clickTab(env, label);
      await settle(env);
      perTab[label] = {
        offered: existed,
        paths: env.network.requests.slice(before).map((r) => `${r.method} ${r.path}`),
      };
    }
    return {
      ...view(env),
      tabsOffered: offered,
      bootRequests,
      perTab,
      requestPaths: env.network.requests.map((r) => `${r.method} ${r.path}`),
      requests: requests(env),
      proof: proofOf(env),
    };
  },

  /**
   * Bypass the gesture and call `renderPanel(id)` directly — what a deep link or a router would
   * do — for a panel whose capability the caller lacks. Reports the requests issued and how the
   * call terminated.
   *
   * This exists because the gesture-level invariant answers "a rendered UI never offers it"; it
   * does NOT answer "the loader would refuse if something else called it". They are different
   * claims and v0.13.0 will have routing.
   */
  async panelWithoutCapability(params) {
    const env = boot(params);
    await settle(env);
    const before = env.network.requests.length;
    const outcomes = {};
    for (const panel of params.panels ?? []) {
      const seen = rejections.length;
      try {
        env.context.renderPanel(panel);
        await settle(env);
        const raised = rejections.slice(seen);
        outcomes[panel] = raised.length ? `threw: ${raised[0].constructor.name}` : "returned";
      } catch (error) {
        outcomes[panel] = `threw: ${error.constructor.name}`;
      }
    }
    return {
      outcomes,
      requestsAfterBypass: env.network.requests.slice(before).map((r) => `${r.method} ${r.path}`),
      panelsPresent: env.document.querySelectorAll(".panel").map((p) => p.dataset.panel),
      proof: proofOf(env),
    };
  },

  /** The instrument's own conformance suite. Nothing about app.js. */
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
