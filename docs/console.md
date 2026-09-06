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

Five operations sit under **Restructure this situation**, and each is a statement:

| control | what it says | what it teaches |
|---|---|---|
| **Move** an alarm to another situation | it does not belong here, and it belongs there | a negative pair against the members it left, and a positive against the ones it joined |
| **Merge** another situation into this one | these are one incident | the cross pairs, from the two memberships as they were |
| **Split marked members out** | these members do not belong with the rest | a negative pair for each marked member against each unmarked one |
| **Save** a name | nothing about the grouping | nothing |
| **clear**, beside a member alarm | this ALARM is stale | nothing |

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
* **what this trap means.** The name appears on every alarm of that class, on **Alarm Classes**,
  and on the timeline. Where you have not named one, the row shows the vendor the appliance
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
* **the health control** — queue depth, p95 latency, the derived trap rate with its window, and the
  two receiver counters that mean loss, with one word summarising them.

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
