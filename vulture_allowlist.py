# Vulture dead-code allowlist (the CI dead-code gate: `make deadcode`).
# These names are referenced in ways vulture's static scan of the runtime package cannot
# see: FastAPI route handlers (registered via decorators), dataclass fields read via
# asdict(), framework-override methods/attributes, the retained v0.2.0 device_affinity
# shim, and methods exercised only from the test suite. Regenerate with:
#   python -m vulture netcorenoc --make-whitelist > vulture_allowlist.py

_.capture_signals  # unused method (netcorenoc/api/models.py)
security_headers  # unused function (netcorenoc/api/perimeter.py)
healthz  # unused function (netcorenoc/api/routes_static.py)
readyz  # unused function (netcorenoc/api/routes_static.py)
_.status_code  # unused attribute (netcorenoc/api/routes_static.py)
login  # unused function (netcorenoc/api/routes_auth.py)
logout  # unused function (netcorenoc/api/routes_auth.py)
me  # unused function (netcorenoc/api/routes_auth.py)
change_password  # unused function (netcorenoc/api/routes_auth.py)
graph  # unused function (netcorenoc/api/routes_read.py)
classes  # unused function (netcorenoc/api/routes_read.py)
situations  # unused function (netcorenoc/api/routes_read.py)
situation  # unused function (netcorenoc/api/routes_read.py)
timeline  # unused function (netcorenoc/api/routes_read.py)
entities  # unused function (netcorenoc/api/routes_read.py)
entity_detail  # unused function (netcorenoc/api/routes_read.py)
state_clears  # unused function (netcorenoc/api/routes_read.py)
feedback  # unused function (netcorenoc/api/routes_operate.py)
change_role  # unused function (netcorenoc/api/routes_admin.py)
get_config  # unused function (netcorenoc/api/routes_admin.py)
set_config  # unused function (netcorenoc/api/routes_admin.py)
read_quarantine  # unused function (netcorenoc/api/routes_audit.py)
read_audit  # unused function (netcorenoc/api/routes_audit.py)
export_audit  # unused function (netcorenoc/api/routes_audit.py)
events  # unused function (netcorenoc/api/routes_events.py)
varbind_name  # unused function (netcorenoc/known_oids.py:195)
_.device_affinity  # unused method (netcorenoc/learn.py:336)
_.msg  # unused attribute (netcorenoc/logsetup.py:37)
_.format  # unused method (netcorenoc/logsetup.py:53)
_.handlers  # unused attribute (netcorenoc/logsetup.py:76)
_.processed  # unused attribute (netcorenoc/main.py:225)
_.processed  # unused attribute (netcorenoc/main.py:368)
_.should_exit  # unused attribute (netcorenoc/main.py:907)
PUBLIC_ROUTES  # unused variable (netcorenoc/rbac.py:79)
received  # unused variable (netcorenoc/receiver.py:51)
accepted  # unused variable (netcorenoc/receiver.py:52)
denied  # unused variable (netcorenoc/receiver.py:53)
quarantined  # unused variable (netcorenoc/receiver.py:54)
_.datagram_received  # unused method (netcorenoc/receiver.py:299)
_.received  # unused attribute (netcorenoc/receiver.py:301)
_.denied  # unused attribute (netcorenoc/receiver.py:303)
_.accepted  # unused attribute (netcorenoc/receiver.py:308)
_.quarantined  # unused attribute (netcorenoc/receiver.py:311)
_.row_factory  # unused attribute (netcorenoc/store.py:80)
_.entity_count_for_ne  # unused method (netcorenoc/store.py:627)
_.set_must_change_password  # unused method (netcorenoc/store.py:852)
_.best_promotable  # unused method (netcorenoc/varbind_profile.py:345)

# v0.6.0 — the scoring seam. FastAPI route handlers (registered by decorator, never called by
# name) and the reserved/read-by-scorer LinkFeatures fields. The reserved slots are deliberately
# unread in v0.6.0: they exist so v0.7/v0.8 features are a minor contract bump rather than a
# breaking change (DECISIONS #49), and `tests/test_scoring.py` asserts they stay None and are
# ignored. `class_i`/`class_j`/`ne_i`/`ne_j` are carried for future scorers and for provenance;
# the built-in AdditiveScorer scores on the affinities already resolved from them.
get_scorer  # unused function (netcorenoc/api/routes_scorer.py)
preview_scorer  # unused function (netcorenoc/api/routes_scorer.py)
set_scorer  # unused function (netcorenoc/api/routes_scorer.py)
rollback_scorer  # unused function (netcorenoc/api/routes_scorer.py)
class_i  # unused variable (netcorenoc/scoring.py:78)
class_j  # unused variable (netcorenoc/scoring.py:79)
ne_i  # unused variable (netcorenoc/scoring.py:81)
ne_j  # unused variable (netcorenoc/scoring.py:82)
severity_i  # unused variable (netcorenoc/scoring.py:86)
severity_j  # unused variable (netcorenoc/scoring.py:87)
topo_distance  # unused variable (netcorenoc/scoring.py:88)
probable_cause_i  # unused variable (netcorenoc/scoring.py:89)
probable_cause_j  # unused variable (netcorenoc/scoring.py:90)
event_type_i  # unused variable (netcorenoc/scoring.py:91)
event_type_j  # unused variable (netcorenoc/scoring.py:92)

# v0.7.0 — governance. FastAPI route handlers (registered by decorator, never called by name),
# plus `role_allows`, which is deliberately no longer called from `src/`.
#
# `role_allows` answers "may this role EVER hold this capability?" — the *ceiling* question. Since
# v0.7.0 the authorization path asks the different question "does this principal hold it right
# now?" and calls `resolve_capabilities` instead, so nothing in the runtime package calls
# `role_allows` any more. It is kept, exported, and documented because it is the oracle the
# generated authorization matrix and the governance-parity test compare the resolver against
# (`tests/test_rbac.py::test_authorization_matrix`,
# `tests/test_governance.py::test_empty_policy_resolves_exactly_to_the_v060_behaviour`): deleting
# it would leave the parity gate with nothing independent to check `ceiling ∩ ∅` against.
get_rbac_policy  # unused function (netcorenoc/api/routes_governance.py)
set_rbac_policy  # unused function (netcorenoc/api/routes_governance.py)
get_scope_policy  # unused function (netcorenoc/api/routes_governance.py)
set_scope_policy  # unused function (netcorenoc/api/routes_governance.py)
role_allows  # unused function (netcorenoc/rbac.py) - the ceiling oracle for the parity gate

# v0.8.0. `feedback_members` is the read path for the server-side membership record — the bag a
# label was about. Nothing in the runtime package reads it yet, because v0.8.0 CAPTURES and does not
# consume: the bias report needs only aggregates, and the consumer is v0.9.0's training loop.
#
# It is kept, exported and tested because it is the accessor that makes the release's headline
# property demonstrable at all — `tests/test_dataset.py::test_the_bag_survives_a_merge_that_
# destroys_every_other_trace` uses it to recover a bag after a merge has destroyed every other
# trace, which is the thing `docs/gates/v0.8.0-phase-0.md` §1 proved impossible before. Deleting it
# would leave that proof with nothing to assert against, and would make a future release re-derive
# a query this one already got right.
feedback_members  # unused method (netcorenoc/store/dataset.py) - the recovered-bag accessor

# The two dataset-retention handlers, for the same reason as the governance handlers above:
# they are registered through `DeclaredRoutes`' decorators, so nothing calls them by name.
get_retention  # unused function (netcorenoc/api/routes_admin.py)
set_retention  # unused function (netcorenoc/api/routes_admin.py)

# --- v0.9.0: shadow mode ---------------------------------------------------------------------
# The measurements the shadow report reads and vulture cannot see reaching, because the report
# addresses them through a dict built by `collect`. Each is a NUMBER THE RELEASE PROMISED TO
# PUBLISH — the online sampling cost in rows and microseconds, and whether a fit ever happened —
# so deleting one to satisfy a dead-code check would delete evidence, not code.
sampled
score_ns
trained
# `Store.shadow_opinions` is the admin-side read of the sampled opinions. Not called from the
# report (which uses the richer `shadow_skew_rows` join) and kept because it is the only way to
# read the table without the join — the surface a later release's evaluator needs.
shadow_opinions
# `LabelledPair` fields carried for provenance rather than for the fit: `incumbent_linked` is the
# champion's decision (a comparison basis, never a target) and `evaluated_at` is the leakage
# vector's clock. Both are read by tests and by v0.10.0's split; neither is a feature.
incumbent_linked
evaluated_at
