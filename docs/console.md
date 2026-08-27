# The console

One static web UI, loaded directly by the browser: **no build step, no npm, no lockfile, no
bundle.** The files a browser fetches are the files on disk. That is a test
(`tests/test_build_step.py`), not an intention.

Seventeen views in three groups, plus an overview and one reachable only by address. **A view you
cannot use is not rendered** — a viewer sees no `Administer` group at all, rather than a group of
disabled controls.

## Operations — what is broken now

| View | The question it answers |
|---|---|
| **Situations** | Correlated groups of alarms, and **why each alarm was grouped** |
| **Network graph** | Learned affinity between network elements |
| **Timeline** | Raises and clears over time, per device |
| **Entities** | What the appliance has learned about each element, and the evidence for it |
| **Alarm classes** | Every trap type it has learned, with no configuration |

## Evidence — what has been learned, and what is refused

| View | The question it answers |
|---|---|
| **Labelling** | Confirm or split a grouping, and what your labels have produced |
| **Corpus** | What capture costs in rows, and the three retention tiers |
| **Judge & promotion** | What the gate decided, why it refused, and the seal's query count |

## Administer — the machine itself

| View | The question it answers |
|---|---|
| **Users** | Accounts and their roles |
| **Service tokens** | Non-interactive credentials, shown once |
| **Settings** | Every parameter, in three classes, with its precedence and its impact |
| **Link scorer** | The formula that decides which alarms group. Preview before you apply |
| **Governance** | Who may do what, and who may see which network elements |
| **Quarantine** | Datagrams the parser refused. **Reading this list is audited** |
| **Audit log** | The hash-chained record of every change, and its verification state |

**Your account** is reachable by address but not offered in navigation: it changes your own
password and nothing else.

## The screen this product exists for

**Situations** is a list of dense cards — id, status, alarm count, age — that expand in place. An
expanded card shows the probable root cause, the member alarms in a compact table with severity
encoded in colour **and** glyph **and** text, and then the section the whole product is for:

> **Why these were grouped**

One row per link, carrying the score, the pair, and **the three named terms with each term's number
beside its bar**: temporal, class affinity, entity affinity. You can answer *"why did the system
group these alarms?"* without leaving the screen.
[`correlation.md`](correlation.md#reading-a-breakdown) is how to read one.

## Settings — three classes, and one has no controls

*Mechanism* is yours to set, with the cost stated beside it. *Hardening-only* you may make stricter
and not looser — the project floor is shown, and a looser value is refused with the reason, by the
console before it is sent and by the appliance if it is sent anyway. *Structural* is a fact with no
control: `seal: 0 queries` has no edit box because it is a guarantee, not a preference.

Every live setting shows three columns — environment default, database override, effective — so
*"why is this value what it is?"* has an answer on screen.

## Nothing destructive happens without a preview

The apply control does not exist until you have asked what would be destroyed. Where a route has no
preview mode, the console says so rather than inventing a count.

## Appearance and keyboard

Dark, light, or your system's preference; compact or comfortable density. Both are remembered in a
cookie that carries a theme name and nothing else. The sidebar is **one tab stop** with arrow-key
navigation, and focus moves into the work area when you navigate.

## Four things the console does not do

Stated here rather than discovered:

* **The detail panel is populated by one view of seventeen.** Clicking something in any view other
  than Situations leaves the panel showing *"Select something to see its detail here."* This is a
  defect, it is measured, and it is v0.15.2's first item —
  [`plans/v0.15.2-console.md`](plans/v0.15.2-console.md).
* **On a phone the detail panel does not exist at all.** The single breakpoint at 760 px hides it,
  which puts the per-term contributions — the product's central claim — out of reach on a narrow
  viewport. Same brief, same release.
* **System health is measured and mostly not shown.** `/api/stats` returns eleven keys; the
  **Overview** renders five of them plus p95 latency and an ingest-gap count. `queue_depth` and the
  `receiver` counters — received, accepted, denied, quarantined, dropped — are served on every poll
  and rendered **nowhere**, and there is no CPU, memory, uptime or trap rate at all. Zabbix and
  Grafana both put this on the first screen an operator sees; here the numbers exist and the screen
  does not. Also v0.15.2.
* **The network graph is not keyboard-operable** and has no screen-reader equivalent beyond its
  label. Everything it shows is on the **Entities** screen as text, and the graph says so. No
  screen-reader testing has been performed.

## How the console is tested

By executing it. `tests/domharness/` links and evaluates the whole ES module graph — including the
vendored bytes `CHECKSUMS.txt` pins — in a DOM under `node:vm`, and drives it against responses
captured from the real server. Five invariants are asserted behaviourally rather than by reading the
source as text: the per-role screen boundary, the partial-split payload, a gesture surviving a
server-sent update, escaping, and least privilege at the client.

It needs **Node ≥ 22 on `PATH` and nothing else**, and `make dom` prints how many tests actually
EXECUTED. Without Node they **skip, loudly** — `27 skipped` rather than `27 passed`, and that
difference is the one to read.

**And it cannot see everything.** The harness cannot see whitespace and cannot see emptiness: v0.13.0
shipped six visual defects with 1428 tests green, and v0.14.0 five with 1542. If you change anything
rendered, open a browser.
