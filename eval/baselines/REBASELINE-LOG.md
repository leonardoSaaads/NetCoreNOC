# Baseline re-cut log

Every entry here was written by `make eval-baseline REASON="…"` (`eval/harness.py --write-baseline
--reason`). The target refuses to run without a reason, so a baseline cannot be re-cut silently.

**Read this before trusting an unchanged `make eval` hash across releases**: the hash compares
*current* against *baseline*, so re-cutting the baseline moves it for a reason that is not a
behaviour change. Each entry records the digest replaced, the digest written, and which aggregate
metrics moved.

## 0.17.1 — v0.2.0.json

- **Reason**: v0.18.0 repairs F76: the built-in scorer no longer applies learned cross-element affinity to a pair on two different network elements whose trap OIDs are in different enterprise subtrees (scoring.cross_subtree_elements). Measured over the whole corpus: dual_incident pairwise_f1 0.6364->1.0000, ari 0.0000->1.0000, over_merge_rate 1.0000->0.0000; the other nine scenarios do not move and under_merge_rate stays 0.0000 on all ten. The gate is a deliberate correlation behaviour change, so the baseline is re-cut with it.
- **Replaced digest**: `f1ff642564635a7e92a8895bc38195e3ddf195229a6a05ce110356b0dd3d9131`
- **New digest**: `c19ccd99fe21d3def3f414db9eef3d594f9f7a5131b06315017cda7eda63dbbb`
- **Aggregate metrics that moved** (8):
  - ari: 0.999928 -> 1.0
  - dedup_ratio: 0.710593 -> 0.715602
  - distinct_alarms: 1952 -> 2252
  - entity_accuracy: 0.032275 -> 0.448046
  - over_merge_rate: 0.03125 -> 0.0
  - pairwise_f1: 0.999955 -> 1.0
  - quarantined: 400 -> 0
  - traps_ingested: 2747 -> 3147

## 0.26.0 — current.json

- **Reason**: v0.26.0 adds eval/corpus/dual_incident_same_vendor.json: two concurrent incidents on one vendor's elements, the case v0.18.0's vendor gate (F76) cannot separate. The additive formula merges them (pairwise_f1 0.6364, ari 0.0000, over_merge_rate 1.0000), measured identically on v0.25.0 at a53e6e1. The ten existing scenarios do not move on any metric. This build ships no model (DECISIONS #422), so the formula decides; the aggregate moves only by the new scenario's contribution.
- **Replaced digest**: `c19ccd99fe21d3def3f414db9eef3d594f9f7a5131b06315017cda7eda63dbbb`
- **New digest**: `1199ae7b3ce20454cc705f1bc6c62cd19bc2b18b1683d7da017bc8f67e66aa3a`
- **Aggregate metrics that moved** (7):
  - ari: 1.0 -> 0.99994
  - dedup_ratio: 0.715602 -> 0.717041
  - distinct_alarms: 2252 -> 2268
  - entity_accuracy: 0.448046 -> 0.45194
  - over_merge_rate: 0.0 -> 0.028571
  - pairwise_f1: 1.0 -> 0.999958
  - traps_ingested: 3147 -> 3163
