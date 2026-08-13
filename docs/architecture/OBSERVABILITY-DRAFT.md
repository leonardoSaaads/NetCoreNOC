# Observability — draft

**Status: `v0.11.0+: planned`. This document specifies and implements nothing.** No logging
framework, no metrics library, no correlation plumbing, no route, no dependency. Building any of it
from this document is a build failure; the document exists so the effort can be *scoped*.

---

## 0. The measurement that motivates it

Across **17 418 lines** of production code there are **nine** logging call sites:

```
$ grep -rhoE '\.(debug|info|warning|error|exception|critical)\(' --include='*.py' src/netcorenoc \
    | sort | uniq -c
      2 .error(
      1 .exception(
      3 .info(
      3 .warning(
```

```
scorer_lifecycle.py:86   warning   "%s; using the built-in default scoring parameters"
engine.py:222            error     (a correlation failure)
runner.py:77             exception "supervised task %r crashed (restart=%s)"
runner.py:204            info      "listening for traps on %s:%d/udp"
runner.py:205            info      "web UI and API on %s://%s:%d/"
runner.py:231            info      "receiver stats: %s"
shadow.py:144            warning   "shadow mode failed (%s); ingestion unaffected"
capture.py:160           warning   "feedback-dataset capture failed (%s); ingestion unaffected"
__main__.py:188          error     (argparse's own, on an unknown command)
```

**Zero `debug`.** Two of the nine are startup banners and one is argparse.

---

## 1. The distinction, which is why the gap was invisible

> **Audit answers "who did what."** NetCoreNOC has this, and it is excellent: hash-chained,
> append-only, with actor, role, source IP, action, object type, object id, outcome and redacted
> details, verifiable end to end by `make audit-verify`.
>
> **Observability answers "what is the system doing."** NetCoreNOC has almost none of it.

Every review of this system has looked at the audit chain, found it strong, and moved on. The two
questions are near-neighbours in a sentence and completely different in what they must record: the
audit chain deliberately holds only **governed acts by principals**, and a trap arriving, a situation
forming, a slow loop starting, or a queue filling are none of those.

**An operator at 3 a.m. asks the second question.** *"Why did nothing group for the last twenty
minutes?"* is not answerable from `audit_event`, because nobody did anything.

What is missing, concretely:

* **no request correlation** across perimeter → store → engine. A slow request and the store
  operations behind it cannot be associated;
* **no dimensional metrics surface.** The route table has `/healthz` (liveness, deliberately
  detail-free) and `/api/stats` (authenticated, a fixed document) and nothing else;
* **no structured event at the points where the system makes a decision.** A merge, a close, a
  scorer change, a capture degradation — each is a moment where behaviour changed, and none emits
  anything a machine can consume;
* **no signal of queue backpressure.** `receiver.stats` is printed at shutdown. A window that has
  been shedding for an hour is visible after the process ends.

---

## 2. What must never be logged — the existing floor, which stays

`logsetup.RedactionFilter` and F3's discipline are **not** a starting point to be revisited; they
are the constraint every proposal below is evaluated against.

Never, at any level, in any proposal:

* credentials, tokens, session identifiers, HMAC keys, password material;
* the contents of a varbind — a value may be anything, including a subscriber identifier;
* an NE name, IP or entity key **on any path a scoped principal can cause to be written**, because a
  log is not scope-aware and a scoped operator triggering a logged error would be writing an
  out-of-scope identifier into a file their RBAC forbids them to read;
* anything that would make a `debug` level unsafe to enable in production. **A debug level nobody
  dares turn on is not observability.**

The last is the design constraint most likely to be lost. `debug` exists to be turned on *while the
incident is happening*, by whoever is awake, without a risk assessment.

---

## 3. A correlation identifier

**The hard constraint first, because it eliminates most designs before they are evaluated.**

> **Principle 4 is not negotiable. Any proposal that adds work to `receiver.datagram_received` is
> out before it is considered.** The trap path never gains a lock, an I/O, or per-packet latency.

That is not a preference about performance. `datagram_received` is the one path with no
back-pressure available to it — a UDP datagram is dropped or it is handled — and it is the path a
100 000-trap burst exercises.

So the correlation identifier is **not** per-trap. A per-trap id would be an allocation, a format and
a write per packet, on the one path that may have none.

The proposal, and the seam it lives on:

| origin | scope | crosses |
|---|---|---|
| **HTTP request** — minted at the perimeter, one per request | that request | perimeter → route handler → store operations → engine calls made *synchronously* by the handler |
| **background pass** — minted once per pass, not per item | one maintenance / slow-loop / retention pass | the pass's own store operations and the events it emits |
| **ingest** | **none** | — |

Ingest gets **no correlation identifier at all**, and the honest consequence is stated rather than
engineered around: *a trap cannot be followed through the system, and it never will be.* What can be
followed is the **batch** — the drain of the queue that a maintenance tick performs — which is a
background pass and already has an id under this scheme. That is a strictly weaker guarantee than
per-trap tracing and it is the one compatible with principle 4.

**How it crosses layers without a dependency**: a `contextvars.ContextVar` set by the perimeter and
read by the log formatter. Standard library, no framework, no signature changes, and — the property
that matters — **nothing on the ingest path ever sets or reads it**, so the cost there is exactly
zero rather than small.

---

## 4. The decision points that deserve a structured event

Each of these is a moment where behaviour changed and nothing currently records it. All are
**engine-layer or background**; none is on the ingest path.

| event | why an operator needs it | the field that makes it useful |
|---|---|---|
| **merge** | two situations became one; the incident count changed; §4 of `DATA-LINEAGE.md` says nothing else records the edge | source, destination, member counts |
| **situation close** | the correlation window ended | id, member count, duration, whether a verdict exists |
| **slow-loop start / finish** | *"is the challenger training or stuck?"* is unanswerable today | run id, corpus size, duration, outcome |
| **scorer config change** | the champion changed underneath every subsequent grouping | old and new fingerprint, actor — **this one is also an audit event, and they are not redundant**: audit records *who authorised*, this records *when behaviour actually changed* |
| **capture degradation** | `capture.py:160` warns once; nothing says it is *still* degraded | the error class, and the duration of the degraded state |
| **retention pass** | rows disappeared; F44 was invisible for a release because nothing said so | per-table counts removed, and the tier that removed them |

**Retention is the entry that would have paid for the whole feature.** F44 deleted every human
verdict seven days after its situation closed, silently, for an entire release. A retention pass that
emitted `{feedback: 3}` would have made it a question somebody asked on the first day.

---

## 5. A metrics surface, and the zero-config question answered explicitly

**The question that has to be answered before the design**: does exposing a metrics surface by
default violate principle 1 or serve it?

> **It serves it, provided the default surface is a property of the appliance and never of the
> network.**

Principle 1 is *"the product offers options but never says you can't"* — it is about the operator not
having to configure the product before it works, not about the product exposing less. A NOC appliance
that cannot be monitored is not zero-config; it is under-specified, and the operator discovers this
at 3 a.m.

The line that makes it safe is the one `/healthz` versus `/api/stats` already draws:

| | exposed by default | authenticated | may name |
|---|---|---|---|
| **liveness** | yes | no | nothing — ok/not-ok, as today |
| **appliance metrics** | **yes** — queue depth, drop count, traps/s, situations open, slow-loop age, degradation flags | **yes**, as `/api/stats` is | the appliance only |
| **network metrics** | **no** | — | would be a scope bypass by construction |

A per-NE or per-device counter is **not** an appliance metric. It is the network, it is exactly what
RBAC scoping exists to partition, and a metrics endpoint has no scope. That is the boundary, and it
is the same one `bias.py` and `agreement.py` hold by reporting aggregates and never names.

**Format is deliberately not decided here.** Prometheus text exposition is the obvious candidate and
it is a *format*, not a dependency — it can be emitted with `str.join`. Whether the project wants to
be scraped is a different question from whether it wants to be measurable, and only the second is
settled above.

---

## 6. The UI's dependency on this

**Every screen worth building needs numbers that today exist only inside one process**, and this is
the reason the UI work cannot be scoped before this document is answered.

| screen | what it needs | where that lives today |
|---|---|---|
| ingest health | queue depth over time, drop rate, window state | `receiver.stats`, in memory, printed at shutdown |
| correlation activity | situations opened/closed/merged per interval | derivable by query, expensively, and never over *time* |
| learning progress | slow-loop last-run age, corpus growth rate, outcome | nowhere — the shadow report is a point-in-time CLI document |
| evidence quality | reconciliation drift, degradation windows | one maintenance check and one `log.warning` |
| "why is nothing grouping" | the answer, which is usually backpressure or a degraded capture | **nowhere at all** |

The last row is the honest summary: **the single most common operational question about this product
has no data behind it.** A UI built before that is fixed would be a set of screens rendering the same
`/api/stats` document in different shapes.

---

## 7. What this document deliberately does not answer

* **Whether metrics are pulled or pushed.** §5 settles what may be exposed and to whom, and nothing
  about transport.
* **Whether a structured event is a log line or a table.** A log line is free and ephemeral; a table
  is queryable, retained, and immediately raises the retention questions in `DATA-LINEAGE.md` §2. The
  merge event in particular is *also* the missing input to `DATA-LINEAGE.md` §4, and if it were a
  table it might be the answer to both — **which is a reason to decide them together and not a reason
  to decide either here.**
* **What the default log level should be.** §2 argues `debug` must be safe to enable; it does not
  argue it should be on.
* **Whether the correlation identifier reaches the audit chain.** Tempting, and it would tie *who did
  what* to *what the system then did*. It also adds a column to an append-only hash-chained table,
  which is a migration and a re-derivation of every existing chain hash. Out of scope for a document
  that changes no schema.
* **Whether any of this is v0.11.0's problem or the UI release's.** The measurement in §0 says the
  gap is real; nothing here says when it is paid for.
