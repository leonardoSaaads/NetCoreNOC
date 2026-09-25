# The console

One static web UI, loaded directly by the browser: **no build step, no npm, no lockfile, no
bundle.** The files a browser fetches are the files on disk. That is a test
(`tests/test_build_step.py`), not an intention.

Eighteen views in three groups, plus an overview and one reachable only by address. **A view you
cannot use is not rendered** — a viewer sees no `Administer` group at all, rather than a group of
disabled controls.

## Operations — what is broken now

| View | The question it answers |
|---|---|
| **Situations** | Correlated groups of alarms, and **why each alarm was grouped** |
| **Network graph** | Learned affinity between network elements, drawn the same way every time; select an element (click, or Tab and Enter) for its situations and recent traps. *Elements by load* ranks the estate exactly, because a node stops growing at a size |
| **Timeline** | Raises and clears over the window you pick, per element, with repeats of one trap folded into one row; window, element, kind and page are in the address, so the screen can be sent to a colleague |
| **Entities** | What the appliance has learned about each element, and the evidence for it |
| **Trap catalogue** | Every trap type, by name and severity: search, browse by vendor branch, name or grade a trap or a whole branch, import a trap list |
| **Maintenance** | Planned work: what is scheduled, what is running, and **what it still collects** |

## Evidence — what has been learned, and what is refused

| View | The question it answers |
|---|---|
| **Labelling** | Confirm or split a grouping, and what your labels have produced |
| **Corpus** | What capture costs in rows, and the three retention tiers |
| **Judge & promotion** | What the gate decided, why it refused, the seal's query count, and the four named quantities over time — **never composed**, and with the three things nothing measures named on the screen |

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

**Your account** is reachable by address but not offered in navigation: it shows who the server
says you are, and changes your own password.

### Passwords, and the two things the appliance will not let you do

Both password forms — the forced change at first sign-in and the change on **Your account** — ask
twice, refuse a mismatch **before sending anything**, and offer a reveal control that is off by
default and reachable from the keyboard. The length indicator beside the new-password field reports
the bound the *server* enforces, served on `/api/me` and beside the forced-change demand; the
console carries no copy of it. The rule is length and only length: 12–128 characters, no
composition requirement, no expiry ([NIST SP 800-63B](https://pages.nist.gov/800-63-3/sp800-63b.html)).

Changing your password **signs out every session the account holds, including the one you are
using.** The screen says so before the click and after it.

The appliance also refuses to remove its own last administrator — a role change, a deletion, and
(when one exists) a disable are all refused while exactly one enabled admin remains. **Users** shows
that account's role as locked rather than offering a control that would fail. If an appliance ends
up with no admin anyway, [`troubleshoot.md`](troubleshoot.md) has the recovery; before v0.15.3 there
was none (F79).

## The Overview: six questions, in the order you ask them (v0.16.7)

The landing screen answers six, top to bottom, and the order is the decision rather than the
layout:

1. **How bad is it** — active alarms by severity: critical, major, minor, warning, `indeterminate`,
   and **unplaced**. The last row is the one to read first on a new appliance, and it is explained
   below.
2. **What is happening** — alarms raised over the range you pick, one line per severity band,
   `unplaced` included. The range reaches the query: *7d* reads seven days.
3. **Where** — the busiest five, and a compact graph of the estate in the same deterministic layout
   as the Graph screen, linking to it.
4. **Which element is worst** — the busiest five, by alarms active **now**.
5. **Is the appliance itself keeping up** — CPU, memory, storage and queue depth as series.
6. **What it has learned** — devices and alarm classes, learned and not configured.

### Why the severity panel may read `—` on every band

**Because the appliance has not been able to place your alarms, and says so rather than guessing.**
It learns severity from the trap stream: a varbind becomes the severity field only when a small
vocabulary match **and** an ordinality check against observed alarm lifetimes agree, and the second
check needs fifty *closed* alarms on the element. Until both agree, every alarm counts under **not
placed** and every band reads `—`.

A `0` would be a different claim — *"I checked every active alarm and none is critical"* — and the
appliance has not checked. Once it has placed something, an empty band reads `0`, and the two
readings mean what they say.

**To fill it now**, declare a severity for an alarm class: *Alarm classes → a class → severity*. A
declaration takes precedence over the learned value at read time, moves every active alarm of that
class at once, and the panel says how many of its bands came from a declaration so the two are
never confused. Nothing overwrites what the appliance learned.

**Every chart names its source and the span it actually covers**, and the span comes from the data
rather than from the window that was requested: ask for seven days on a busy appliance and the axis
will tell you it is showing the most recent minute, because the read is bounded at a thousand
alarms. A metric the host will not give up renders `—` and the words *not measured*, never `0`; a
gap in a series **breaks** the line rather than being drawn through.

Three things that would be reasonable to expect and are **not** there, because nothing measures
them: **top elements over a chosen week** (the appliance counts alarms active now, not a count over
a window), **severity over time** (measured on this project's own corpus, the whole alarm history
spans 1.14 seconds and nothing has cleared, so a time axis would be a claim about a corpus rather
than a statement about the data), and anything derived from the sampled shadow opinions. All three
are recorded in [`plans/releases.md`](plans/releases.md) with the table, the columns and the route
parameter a later release would need.

The **queue depth** series is derived in your browser between polls, on the same footing as the trap
rate: the appliance serves the number and keeps no history, so the series starts when you open the
console and is lost on reload. The chart says so.

## The screen this product exists for

**Situations** is a list of dense cards — id, status, alarm count, age — that expand in place. An
expanded card shows the probable root cause, the member alarms in a compact table with severity
encoded in colour **and** glyph **and** text, and then the section the whole product is for:

> **Why these were grouped**

One row per link, carrying the score, the pair, and **the three named terms with each term's number
beside its bar**: temporal, class affinity, entity affinity. You can answer *"why did the system
group these alarms?"* without leaving the screen.
[`correlation.md`](correlation.md#reading-a-breakdown) is how to read one.

## Working a situation (v0.16.0)

A card carries **three states**, and the tabs above the list are those states: **new** (the
correlator formed it and nobody has looked), **open** (an operator has touched it), **resolved**
(it is finished, and `resolution` says why — `operator`, `self_cleared`, `idle`, `merged`,
`manual_clear`). *"The network fixed it"* and *"nobody looked at it for an hour"* are different
facts and the console now says which.

**A card leaves the New tab the moment you gesture on it.** That is the state machine working, not
the card vanishing: it is on Open, and on Any.

Every operation on a card is a **statement**, and the card is laid out so the statement comes
first: one row of decisions above the member table, the table itself, and then the two questions
you only sometimes ask — *why did it group these* and *is the grouping shaped wrong*.

| control | what it says | what it teaches |
|---|---|---|
| **Confirm grouping** | these belong together | every pair in the situation, positive |
| **Grouping is wrong** | they do not | the marked members, negative against the rest; with nothing marked, a plain split |
| **Start working this** | somebody has it | nothing about the grouping |
| **Move N elsewhere** → a situation | they do not belong here, and they belong there | a negative pair against the members they left, and a positive against the ones they joined |
| **Move N elsewhere** → *A new situation* | these do not belong with the rest | a negative pair for each marked member against each unmarked one |
| **Merge another situation in** | these are one incident | the cross pairs, from the two memberships as they were |
| **Save** a name | nothing about the grouping | nothing |
| **clear**, beside a member alarm | this ALARM is stale | nothing |

**Move and Merge ask for the selection first.** With nothing ticked there is nothing to move, so
the control is not offered and one line says what to tick. Tick members and press **Move**, and
the situations that exist are listed — id, name, size, age — filtered by id or name if there are
many. *A new situation* is the first row, and it is what an operator-split is: move these
somewhere that does not exist yet. There is no situation id to type and none to remember.

**`Split (wrong grouping)` is now `Grouping is wrong`.** Same gesture, same route, same evidence:
`split` is the name of the route and the question you are answering is whether the appliance got
the grouping right.

The last two are the release's central distinction. A hand-clear is a fact about an alarm's
lifecycle, not about whether the grouping was right, so it carries **no confidence control** and
reaches the correlator through nothing.

**How sure are you?** The slider is on the card because a gesture is evidence, and evidence that
cannot say how sure it was is evidence you cannot weigh later. It shows three things at once: the
percentage you chose, the weight it produces (`0.6 + 0.4 x c`, so 80 % counts at 92 %), and, below
50 %, a warning that the action will still happen and will teach nothing. Your confidence is stored
**per gesture and per operator**, exactly as you gave it — it is never folded into a weight at rest,
so a later release can check whether your stated 0.8 corresponds to being right eight times in ten.

**What has been done to this situation** is the card's third section: every gesture, its actor, its
confidence and its age, appended and never edited. The situation's `id` is still its identity and
its permalink still works; a name is a label on top of it.

## Telling the appliance what you already know

The appliance starts knowing nothing about your network and learns it from the trap stream. Three
things it cannot infer, and until v0.16.3 had nowhere for you to write down, are declared from the
member row itself — the row where the trap appears, while you are looking at it:

* **which equipment this is.** The name appears on this row, on **Entities**, and on the **Network
  Graph**, because all three read the same record. It is a label, not a rename: correlation is
  keyed on the address and is unaffected.
* **what this trap means.** The name appears on every alarm of that class, on the **Trap
  catalogue**, and on the timeline. Where you have not named one, the row shows the vendor the appliance
  resolved from the OID's enterprise arc, beside the OID — a vendor is not a name, so it never
  takes the name's place.
* **how serious it is.** Per kind of trap, from the five severities the appliance renders.

**What you declare wins, and what the appliance learned is kept.** The pill marks a declared
severity and names the learned one in its tooltip, and *Clear* puts the appliance's own value back.
Nothing you declare here teaches the correlator anything: a name is not a claim about which alarms
belong together, and a severity is a claim about a kind of trap rather than about a link.

**One interruption, and only one.** If the appliance has *learned* a severity here — which takes
200 observations and 50 closed alarms whose lifetimes confirmed the ordering — and your declaration
is two or more steps away from it, the row asks you to confirm and shows you what it learned.
Cancel writes nothing. Anything closer than two steps is saved without a word.

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

Dark, light, or your system's preference; compact or comfortable density; and since v0.16.4 the
sidebar collapses to icons. All three are remembered in cookies that carry a name from a closed set
and nothing else — never a user id, never a token ([#172](adr/DECISIONS.md), [#290](adr/DECISIONS.md)).

The sidebar is **one tab stop** with arrow-key navigation, and focus moves into the work area when
you navigate. Collapsed it is icon-only, so every item keeps its label in the accessible tree and
gains an explicit name carrying its badge — a collapsed rail's accessible name is the whole of its
usability for a screen-reader operator.

## The top bar: what it holds, and what it stopped holding

Until v0.16.4 it carried four counters — devices, classes, active alarms, open situations. At
390 px they wrapped it onto four rows, and with the nav strip and the warning banners **360 px of
an 844 px phone** were spent before the work area began. They are gone; it is 94 px now.

What replaced them is two disclosures:

* **the bell** — every operator warning, each on its own line, with a link to the setting that
  resolves it where one exists. Three of the ten warnings this appliance can emit name a parameter;
  the other seven render as text with no link, because a control that navigates somewhere unhelpful
  is worse than none.
* **the health control** — one word summarising whether the appliance is keeping up, then CPU,
  memory and storage as three meters with a two-hour sparkline each, then queue depth, p95 latency
  and the derived trap rate on one secondary line. The three host readings are stdlib reads
  (`/proc/stat`, the cgroup's limit, `os.statvfs`) and the dependency count is still five. It opens
  on hover as well as on click, because health is a glance; the bell keeps click only, because a
  warning is something you act on.

**A warning can be snoozed, not deleted** (v0.22.0): for yourself, for 24 hours, 7 days, or until
its text changes. A security-posture warning — traffic accepted from anywhere, a console in clear
text — can only be snoozed for a time, and an admin can always see who snoozed one. The bell keeps
a muted count of what is snoozed, and *Restore* brings it back.

An **ingest gap** is still a banner above the work area as well as being in the bell: a panel an
operator has to open is the wrong home for *"traps are being lost now"*.

## Which clock every time is in

Every absolute timestamp reads `2026-09-06 14:32:07 -03:00` — your browser's zone, with **the
offset from UTC in the text**, not only in a tooltip. The top bar names the zone itself, because a
name and an offset are different facts: the first says whose clock, the second makes the arithmetic
against a UTC log trivial. The database stores epoch UTC.

**A trap's timestamp is when the appliance received the datagram**, not when the equipment raised
the alarm — [`operate.md`](operate.md) says why that matters after an incident.

## Two regions, and there used to be three

The shell is a navigation sidebar and a work area. Until v0.15.2 there was a third — a 320-pixel
detail panel — and **no view ever wrote to it**: it showed *"Select something to see its detail
here."* on all seventeen screens, permanently, and was hidden outright below 760 px. It is removed
rather than completed ([decision #219](adr/DECISIONS.md)), because what a selection would have
shown is already in the expanded card, in place. The work area is 320 px wider on every screen.

## Two things the console does not do

Stated here rather than discovered:

* **The network graph is not keyboard-operable** and has no screen-reader equivalent beyond its
  label. Everything it shows is on the **Entities** screen as text, and the graph says so. No
  screen-reader testing has been performed.
* **There is no CPU, memory, disk or uptime figure**, because the appliance does not measure one —
  there is no `psutil`, no `resource` and no `/proc` read anywhere in `src/`. What it *does* measure
  is in the top bar's health control on every screen since v0.16.4, and that control says which
  four things it shows rather than leaving the absence to be inferred. A ten-minute series is
  v0.16.5's, because it needs storage nothing has.

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

## Maintenance: declaring planned work (v0.21.0)

A **maintenance window** says *"this equipment is going quiet on purpose, between these two
instants, in this time zone."* A target under a window is **not collected by default**; what still
gets through is the set of rules you write.

### The form is four cards, one at a time

Never two hundred fields at once. Each card collapses to a one-line summary, so you can see where
you are without scrolling:

1. **What** — a name, an optional description, the organization, and who may see the details.
2. **When** — start, end, the time zone, and the patch band either side. The bar under the fields
   draws the window and both patch bands to scale; it is hand-written SVG, so the test suite can
   read each band's width as a number rather than look at a picture.
3. **Where** — the elements. The card shows a live count of devices and active alarms this would
   affect, computed by the **same code the API's `preview` runs**, so the number you read and the
   number an agent reads cannot disagree.
4. **What still gets through** — the per-target rules.

### The time zone is mandatory, and it is a zone rather than a city

Search for the city you think in: *Brasília*, *Washington*, *Beijing*. The picker shows the city
and stores the canonical IANA zone (`America/Sao_Paulo`, `America/New_York`, `Asia/Shanghai`) —
none of those three cities has a zone of its own, and a window stored under a label its `zoneinfo`
cannot load would schedule at the wrong hour. Every time is shown twice: **site time** and **your
time**, because the person declaring the work and the person on shift are often not in one place.

### Three kinds of rule, and how they compose

| Rule | *"Collect only…"* |
|---|---|
| **Severity** | …alarms at or above a severity |
| **OID subtree** | …traps whose trap-OID, or whose varbinds, sit under an arc |
| **Time slot** | …traps inside a narrower window than the whole |

> **Rules of different kinds are ANDed; rules of the same kind are ORed.**

So *"only critical alarms"* on one host and *"only traps between 11:15 and 11:20, and only OIDs
under `1.3.6.1.4.1.2011.5.25.31.1.1.1.1`"* on another is one window with two targets — and the
second host's trap at 11:17 on an unrelated OID does not pass, because the slot and the subtree
intersect. Two subtrees on one target are a union, because you wrote the second one to admit more.

An OID rule matches on **arc boundaries**. A rule for `…1.1.1.1` admits `…1.1.1.1.4.2` and does
**not** admit `…1.1.1.10`: the tenth column of a table is not inside the first column's subtree.

### Over six hours, somebody has to agree

Up to six hours the window takes effect as scheduled. Over six hours it waits in **Pending
confirmation** and **suppresses nothing at all** until an editor or an admin confirms it — if
nobody does, it expires having suppressed nothing, which is the safe direction. A window an **agent**
created always waits for a human; a window you created yourself you may confirm yourself, with an
explicit second gesture. A deployment that wants two different people can withhold the confirm
capability from the role that holds the write capability; they are separate for that reason.

### What happens to a fault that outlives the window

The appliance keeps a **state ledger** while a window is in force: per element, class and instance,
whether it saw a raise and whether it saw a clear. Six numbers, and **no varbinds, no severity and
no payload** — recording those would be collecting the trap you asked it not to collect.

When the window closes, anything raised inside it that never cleared **surfaces as an alarm**,
marked *"raised during maintenance, still active"*. That alarm carries **no severity**, and the
reason is worth knowing: the appliance is not failing to place one, it never saw the trap. You can
turn the ledger off per window; the card says in plain words what you lose if you do.

The marker stays while the alarm is still active and has not been reported again since the window
closed; an editor who has seen it can acknowledge it (the ✓ beside it), which records who and when
and leaves the alarm exactly where it is.

### Changing a window that is running

Open the window's row. A running window can be **ended now**, **extended** (+30 min, +1 h, +2 h) or
**shortened**, and its name, description and visibility edited. Its start, zone, patch band,
targets and rules cannot change while it is in force — end it and schedule another — because the
minutes already past must answer to the window that was actually in force.

### The marker every role sees

A device or a situation under planned work carries a badge saying so, with how long is left — on
**every** screen, for **every** role, including a viewer looking at a window whose details are
restricted. A host that goes quiet with no marker reads as a healthy host, which is the one way
this feature could make an outage harder to see. What the window *is* — its name, its owner, its
rules — follows its visibility setting. That it exists does not.

## Explanations live behind the `i` (v0.22.0)

A sentence the console still needs sits behind a small **i** beside the thing it explains, never as
a paragraph above it. The **i** is a button: Tab reaches it, Enter or Space opens it, Escape closes
it, and a screen reader hears the text on focus without opening anything. Nothing is hover-only.

