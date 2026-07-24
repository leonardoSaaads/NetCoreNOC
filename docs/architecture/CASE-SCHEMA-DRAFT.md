# CASE schema — draft (specification only, not implemented in v0.3.0)

This document defines the versioned JSON contract that will eventually cross the boundary
from NetCoreNOC (correlation and inference) to the downstream systems that consume it — first
automated testing, later ticket creation. **It is not implemented in v0.3.0.** It is written
now because it defines what v0.4.0 and v0.5.0 must *produce*; specifying it last would
guarantee rework in all three versions.

Every field is tagged with the version that first populates it:

- `v0.3.0: available` — the data exists in the store today and could be serialised now.
- `v0.4.0: planned` — depends on typed relations / archetypes (SCOPE-0.3 §"out of scope" 1–2).
- `v0.5.0: planned` — depends on subsumption, impact scope, or pattern recurrence (3–5).

Producing the contract is v0.6.0 (SCOPE-0.3 §"out of scope" 6). The roadmap is therefore
legible from the schema itself: a v0.3.0 producer would emit the `available` fields and leave
the `planned` ones `null` or omitted, and consumers must tolerate that.

## Envelope

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `schema_version` | string (semver) | v0.3.0: available | Contract version, e.g. `"1.0.0"`; consumers reject unknown majors. |
| `case_id` | string | v0.3.0: available | Stable case identifier (`"case-<situation_id>"` initially). |
| `state` | enum | v0.3.0: available | Lifecycle: `open` \| `acknowledged` \| `closed` \| `merged`. Mirrors the situation lifecycle. |
| `created_at` / `updated_at` | number (epoch seconds) | v0.3.0: available | First and last activity. |
| `root` | object | v0.3.0: available | The probable root cause (below). |
| `affected_entities` | array\<object\> | v0.3.0: available | Every entity with an alarm in the case (below). |
| `relation` | object | v0.4.0: planned | The inferred relation binding the case (typed-relations placeholder). |
| `severity` | object | v0.3.0: available (`unknown` allowed) | Rolled-up severity (below). |
| `timeline` | array\<object\> | v0.3.0: available | Member alarms in time order (below). |
| `links` | array\<object\> | v0.3.0: available | Accepted correlation links with their three score terms. |
| `pattern` | object | v0.5.0: planned | Recurrence identity and statistics (placeholder). |
| `evidence` | object | v0.3.0: available | Free-form, additive; never load-bearing for a consumer. |

## `root`

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `ne_ip` | string | v0.3.0: available | Reporting NE (source IP). |
| `entity_key` | string | v0.3.0: available | The alarmed entity; the NE's IP at level 0. |
| `entity_level` | integer | v0.3.0: available | 0 = the NE itself; deeper = a contained entity. |
| `alarm_class_oid` | string | v0.3.0: available | Trap OID of the root alarm, as an opaque token. |
| `alarm_class_name` | string \| null | v0.3.0: available | Human name if a standard OID, else `null`. |
| `confidence` | number [0,1] | v0.3.0: available | Root-cause precedence margin (root_top1 basis). |
| `key_source` | string | v0.3.0: available | The varbind OID that identified the entity, or `"self"` at level 0. |

## `affected_entities[]`

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `ne_ip` | string | v0.3.0: available | Reporting NE. |
| `entity_key` | string | v0.3.0: available | The alarmed entity. |
| `parent_key` | string \| null | v0.3.0: available | Containment parent (`null` at level 0); from the FD hierarchy. |
| `level` | integer | v0.3.0: available | Containment depth. |
| `key_source` | string | v0.3.0: available | Chosen discriminator varbind OID, or `"self"`. |
| `confidence` | number [0,1] | v0.3.0: available | Promotion score of the discriminator. |
| `alarm_count` | integer | v0.3.0: available | Alarms on this entity in the case. |
| `impact_estimate` | object \| null | v0.5.0: planned | "Probably affected equipment" (impact-scope placeholder). |

## `relation` (v0.4.0: planned)

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `type` | enum | v0.4.0: planned | Placeholder enum: `unknown` \| `physical_adjacency` \| `containment` \| `common_cause_site` \| `shared_upstream`. In v0.3.0 always `unknown` (or the object omitted). |
| `confidence` | number [0,1] | v0.4.0: planned | Relation confidence. |

## `severity`

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `value` | string \| null | v0.3.0: available | Rolled-up worst severity token, or `null` when unknown — never fabricated. |
| `rank` | integer \| null | v0.3.0: available | Normalised 0–4, 0 most severe; `null` when unknown. |
| `source` | enum | v0.3.0: available | `learned` \| `bundled_vocabulary` \| `unknown`. |

## `timeline[]`

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `alarm_id` | integer | v0.3.0: available | Member alarm. |
| `entity_key` | string | v0.3.0: available | Its entity. |
| `class_oid` | string | v0.3.0: available | Its alarm class. |
| `raised_at` | number | v0.3.0: available | First-seen epoch seconds. |
| `cleared_at` | number \| null | v0.3.0: available | Clear time, or `null` if active. |
| `severity` | string \| null | v0.3.0: available | Per-alarm severity, or `null`. |
| `state_value` | string \| null | v0.3.0: available | Learned state-field value at raise, if any. |

## `links[]`

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `alarm_a` / `alarm_b` | integer | v0.3.0: available | The linked alarms. |
| `score` | number | v0.3.0: available | Total link score (> threshold). |
| `term_t` / `term_a` / `term_e` | number | v0.3.0: available | Temporal, class-affinity, entity-affinity terms — the explanation. |

## `pattern` (v0.5.0: planned)

| Field | Type | Availability | Meaning |
|---|---|---|---|
| `pattern_id` | string \| null | v0.5.0: planned | Fingerprint identity of the situation shape. |
| `occurrences` | integer \| null | v0.5.0: planned | Times this pattern has recurred. |
| `last_seen` | number \| null | v0.5.0: planned | Previous occurrence epoch seconds. |

## `evidence`

A free-form object, additive and never load-bearing: a consumer must function if it is empty.
Intended for auditing hints — the profiler scores that drove promotion, the FD support
counts, the storm/damping flags at correlation time.

## Worked example (a v0.3.0 producer)

A PON power outage: the OLT `10.10.0.1` reports its own PSU failure (root) and hundreds of
ONU dying-gasp alarms. `relation` and `pattern` are `null` because typed relations and
recurrence are not yet inferred.

```json
{
  "schema_version": "1.0.0",
  "case_id": "case-4821",
  "state": "open",
  "created_at": 1732531200.0,
  "updated_at": 1732531260.5,
  "root": {
    "ne_ip": "10.10.0.1",
    "entity_key": "olt-psu-A",
    "entity_level": 1,
    "alarm_class_oid": "1.3.6.1.4.1.2011.6.128.1.1.1.1",
    "alarm_class_name": null,
    "confidence": 0.82,
    "key_source": "1.3.6.1.4.1.2011.6.128.1.1.1.9"
  },
  "affected_entities": [
    {
      "ne_ip": "10.10.0.1", "entity_key": "olt-psu-A", "parent_key": null,
      "level": 1, "key_source": "1.3.6.1.4.1.2011.6.128.1.1.1.9",
      "confidence": 0.82, "alarm_count": 1, "impact_estimate": null
    },
    {
      "ne_ip": "10.10.0.1", "entity_key": "onu-42", "parent_key": null,
      "level": 1, "key_source": "1.3.6.1.4.1.2011.6.128.1.1.2.43.1",
      "confidence": 0.88, "alarm_count": 3, "impact_estimate": null
    }
  ],
  "relation": null,
  "severity": { "value": "critical", "rank": 0, "source": "bundled_vocabulary" },
  "timeline": [
    {
      "alarm_id": 9001, "entity_key": "olt-psu-A",
      "class_oid": "1.3.6.1.4.1.2011.6.128.1.1.1.1",
      "raised_at": 1732531200.0, "cleared_at": null,
      "severity": "critical", "state_value": null
    },
    {
      "alarm_id": 9002, "entity_key": "onu-42",
      "class_oid": "1.3.6.1.4.1.2011.6.128.1.1.2.2",
      "raised_at": 1732531200.4, "cleared_at": null,
      "severity": "critical", "state_value": null
    }
  ],
  "links": [
    {
      "alarm_a": 9001, "alarm_b": 9002, "score": 0.71,
      "term_t": 0.29, "term_a": 0.14, "term_e": 0.28
    }
  ],
  "pattern": null,
  "evidence": {
    "entity_discriminator_score": { "R": 0.67, "X": 1.0, "D": 1.0, "S_entity": 0.88 },
    "storm_at_correlation": true
  }
}
```
