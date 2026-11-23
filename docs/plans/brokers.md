# Brokers: one appliance, many networks (ADRs #494–#498)

**Status:** built (ADRs #494–#498). This plan is the reference for the change; the ADRs record each decision
as it lands.

## Why

Until now the Broker plane treated every trap as part of one network. A provider A and a provider B
shared one correlation window, one learned co-occurrence, one champion model, one Network graph and
one Timeline. Two problems follow from that:

* **Precision.** A's co-failures moved B's class affinities and episodes, and a model the judge chose
  on A's labels decided B's groupings.
* **Readability.** The map, the Timeline and the element list mixed two networks.

The NOC plane already separates by customer (its book: instructions, workbook, documents, circuits,
access, email). This change gives the Broker plane the same separation, one level finer: the
**broker**.

## What a broker is

A broker is **one monitored network**, owned by one customer. A customer can have several brokers;
"Claro Brasil" might have one broker for the North-East and one for the South. Each broker is like a
Zabbix proxy: it is where a set of devices is monitored.

* **Identity.** A network element is identified by **(broker, IP address)**, not by its address
  alone, so two customers' private ranges can both contain 10.0.0.1.
* **The Default broker** (id 1) holds everything that existed before this change, and every trap
  that matches no other broker.

### Three ways a trap reaches a broker (chosen when the broker is created)

| Mode | How a trap is attributed | Repeated IPs across brokers |
|---|---|---|
| **Port + ranges** (default) | The broker has its **own UDP port** on this server (e.g. North-East 1162, South 1163). A trap on that port is the broker's. On the standard port, the broker's CIDR ranges claim matching sources. | Yes, on its own port |
| **Remote proxy** | A small NetCoreNOC proxy runs in the customer's network (`python -m netcorenoc proxy`). It receives traps, spools them locally (so nothing is lost while the link is down), and forwards them over HTTPS, authenticated by a token (`POST /api/brokers/ingest`). The token names the broker. | Yes |
| **Ranges only** | One port. The broker's CIDR ranges claim matching sources on the standard port; the longest prefix wins. | No; this limit is stated in the console |

The ranges of two brokers may not be identical. A more specific range wins over a wider one, as in
routing.

**Spool records.** The legacy `R` record means "the standard port". The worker writes `P` (the
local port) for a broker's own port. The central server writes `X` (an explicit broker) for
proxy-forwarded traps. The decoder carries `port` and `broker` on the event, and the engine resolves
the broker from an immutable routing snapshot. The snapshot is rebuilt at the reload point, like
the maintenance-window index: no query and no lock per trap.

## What is per broker, and what stays shared

| Per broker | Shared by every broker |
|---|---|
| The correlation window, recall, episodes and grouping (a situation never spans two brokers) | The trap catalogue: classes, names, severities, imported lists, raise/clear pairs, state clears |
| Learned class and element affinities (the `Learner`'s two matrices) | The packaged league (seven model families) and their offline scorecards |
| **The decider**: mode (league / site model / formula) and an optional pinned member | The additive formula's configuration (a fail-safe) |
| **The judge's champion**, re-ranked on that broker's own labels | Autonomy settings (the acts still run under each broker's champion) |
| The challengers' live shadow | The trap pipeline, the spool, the receiver and the SNMP policy |
| Site models (trained on that broker's labels) | Users, roles, the audit log |
| Situations, the Network graph, the Timeline, the elements | |
| Maintenance windows (each window targets one broker; the list shows all of them) | |

A new broker starts on the packaged league in its offline order. The judge chooses from that
broker's labels as soon as it has enough of them, which is exactly how a new appliance starts today.

## Access

A visibility policy may name brokers: the selector `broker:<id>`, next to `ne:`, addresses, CIDRs
and globs. A person limited to Claro's brokers sees only their situations, map, Timeline and
elements. Every input stays admin-written (F35): only an admin creates a broker or moves an element.

## The console

* **Sidebar, Broker plane:**
  * **Operations:** Overview, Situations, Trap catalogue, Maintenance.
  * **Brokers:** All brokers, then each broker by name. Under the open broker: Network graph,
    Timeline, Entities, Maintenance, Learning.
  * **Administer:** unchanged.

  Network graph, Timeline, Entities, Learning and Judge no longer exist outside a broker.
* **Overview:** a selector at the top right, **All brokers** or one broker, as a Grafana dashboard
  variable with "All".
* **Situations:** a broker filter and a broker column. A manual merge across brokers is refused.
* **Maintenance:** every window of every broker, a broker filter, and "Schedule" for one broker.
* **A broker's Learning:**
  * what waits for judgement, and the corpus;
  * the league as this broker's judge ranks it;
  * the decider (league, pinned member, site model or formula);
  * site training.
* **All brokers:** the list, ordered by name; adding a broker is a three-step wizard (mode, network,
  review). For a remote proxy, the token is shown once along with the command to run.

## Tests that must pass before this is called done

* Unit and API tests for each mode, each resolution rule, (broker, IP) identity, the scope
  selector, per-broker deciders, and the refusal of cross-broker merges.
* A soak with simulated estates run together for hours:
  * a large ISP (thousands of elements, storms);
  * a small ISP;
  * a corporate network;
  * two customers sharing 10.0.0.0/8;
  * a proxy whose link drops and returns.

  Checked against the database: no situation spans brokers, every element sits in the broker its
  traps came through, the spool drains, and there are no lost traps.
* Every screen in a real browser (light and dark; 1440, 1920 and 390 px), and the Compose stack with
  the broker ports published.
