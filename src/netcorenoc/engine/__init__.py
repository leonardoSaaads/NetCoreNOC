"""Engine: the domain, in six subpackages.

What a situation is, what links two alarms, what an entity is, what the root cause is —
and, since v0.14.0, which model decided it and whether that model may be promoted.

* `correlate/` — the correlation decision and the vocabulary it is made in. Imports no
  other domain.
* `dataset/`   — the feedback dataset: how a row enters it, what it means, what is kept.
* `model/`     — the model family: the kinds, the shared fit, the artefact, the dispatch.
* `evaluation/`— shadow mode, the estimator, the judge, the folds, the promotion gate.
* `report/`    — the three deterministic CLI reports. Nothing but the CLI imports it.
* `operate/`   — the running appliance: the batch loop and the periodic work off it.

The order above is the import order, and it is a **strict** one: measured over the whole
package, no domain imports a domain that imports it back (DECISIONS #208).
"""
