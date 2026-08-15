# NetCoreNOC build targets. Override PYTHON to use another interpreter, e.g. in CI:
#   make qa PYTHON=python
PYTHON ?= .venv/bin/python

.PHONY: qa lint typecheck test coverage security deadcode checksums linkcheck run replay loadtest burst \
	fmt migrate audit-verify dist dist-image release-check eval eval-baseline corpus sim \
	bias-report dataset-stats agreement-report shadow-report census

qa: lint typecheck deadcode test eval

security:
	$(PYTHON) -m bandit -q -c pyproject.toml -r src/netcorenoc tools
	$(PYTHON) -m pip_audit

# Dead-code gate (§7): vulture over the runtime package with a committed allowlist.
deadcode:
	$(PYTHON) -m vulture src/netcorenoc vulture_allowlist.py

# Vendored third-party asset integrity (§A.6): fail if d3's bytes drift from the pinned SHA-256.
checksums:
	$(PYTHON) -m pytest -q tests/test_supply_chain.py

# Structure guard + documentation link check (v0.5.0): src/ layout, import resolution, and no
# broken relative Markdown links. Also runs as part of `make test`.
linkcheck:
	$(PYTHON) -m pytest -q tests/test_structure.py

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov=netcorenoc --cov-report=term-missing

# The DOM harness (v0.12.0), reported on its own. `make qa` already runs these — they are ordinary
# pytest tests — and this target exists for ONE reason: so a gate can quote the number of DOM tests
# **executed** rather than collected.
#
# On a machine with Node this prints "N passed". On a machine without it prints "N skipped", and
# that difference is the whole point: a harness that skipped everywhere and was reported as green
# is the most likely way this release fails (SCOPE-0.12.0 §7). Never quote a collected count.
dom:
	$(PYTHON) -m pytest -q -m dom -p no:cacheprovider

# THE ONE TRUE COVERAGE COMMAND (v0.10.1, A3). `make test` above and this target report the same
# percentage — `term` and `term-missing` differ only in the trailing "Missing" column — and this one
# exists so that the figure a gate document quotes has a single, named provenance.
#
# **Quote the `Total coverage:` line and nothing else.** That line is coverage.py's own
# `percent_covered`. The TOTAL row above it prints four columns, and computing the percentage from
# them BY HAND is how v0.10.0 came to record 96.20 % for a tree coverage.py measures at 95.95 %:
# the `BrPart` column counts partially-covered STATEMENTS (123 here), and the percentage divides by
# missing branch ARCS (143 here) — a statement with two unexercised exits is 1 and 2 respectively.
# Recomputing a number the tool already printed is how the two come to disagree with nobody noticing.
coverage:
	$(PYTHON) -m pytest -q --cov=netcorenoc --cov-report=term

fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

run:
	$(PYTHON) -m netcorenoc.main

# Replay the fiber-cut fixture against a locally running NetCoreNOC (port 1162 by default
# so it works without privileges; export NETCORENOC_TRAP_PORT=1162 for the server too).
replay:
	$(PYTHON) tools/trap_replay.py tests/fixtures/fiber_cut.json --port $${NETCORENOC_TRAP_PORT:-1162}

# 1000 traps/s for 60 s against a locally running NetCoreNOC.
loadtest:
	$(PYTHON) tools/trap_replay.py --synthetic 20 --classes 10 --rate 1000 --duration 60 \
		--port $${NETCORENOC_TRAP_PORT:-1162}

# A 100 000-trap burst against a locally running NetCoreNOC (the v0.3.0 window/backpressure
# guard; the pass/fail assertion lives in tests/test_perf.py::burst).
burst:
	$(PYTHON) tools/trap_replay.py --synthetic 50 --classes 20 --rate 100000 --duration 1 \
		--port $${NETCORENOC_TRAP_PORT:-1162}

# Replay the labelled corpus offline and print the delta against the frozen baseline.
# Exits non-zero on a gated regression (pairwise_f1, ari, entity_accuracy).
eval:
	$(PYTHON) eval/harness.py

# Freeze the current metrics as a baseline (Phase 1 only writes eval/baselines/v0.2.0.json).
eval-baseline:
	$(PYTHON) eval/harness.py --write-baseline eval/baselines/v0.2.0.json

# Regenerate the labelled corpus from its deterministic generator.
corpus:
	$(PYTHON) eval/corpus_gen.py

# Run a declarative DSL scenario (trap simulator) against a locally running NetCoreNOC over UDP.
# SCENARIO defaults to login_burst; list options with `python tools/trap_sim.py --list`.
sim:
	$(PYTHON) tools/trap_sim.py $${SCENARIO:-login_burst} --send \
		--port $${NETCORENOC_TRAP_PORT:-1162}

# Apply pending schema migrations to NETCORENOC_DB (idempotent; runs at startup too).
migrate:
	$(PYTHON) -c "import asyncio, os; from netcorenoc.store import Store; \
		s=Store(os.environ.get('NETCORENOC_DB','netcorenoc.db')); \
		asyncio.run(s.open()); asyncio.run(s.close()); print('migrations applied')"

# The feedback-dataset bias report (v0.8.0). Beside `make eval` deliberately: both are
# deterministic offline reports over frozen inputs, and both are GATES rather than dashboards —
# `tests/test_bias.py` compares this output byte-for-byte against a frozen expectation, so it goes
# red the day capture changes shape. Emits aggregates only; reads NETCORENOC_DB.
bias-report:
	$(PYTHON) -m netcorenoc dataset bias

# v0.9.0's primary deliverable, and it needs no model: how well the built-in scorer ALREADY agrees
# with the operators, conditioned by bag size, storm, mixed-vs-uniform, scope, operator and capture
# provenance. Beside `make bias-report` for the same two reasons — deterministic offline report over
# frozen inputs, and a GATE rather than a dashboard (`tests/test_agreement.py` compares it
# byte-for-byte). Emits aggregates only; reads NETCORENOC_DB.
agreement-report:
	$(PYTHON) -m netcorenoc dataset agreement

# The shadow-mode report (v0.9.0): the sufficiency verdict FIRST, then both label-derivation
# policies, partition-level over/under-merge against the human verdicts, bag-level calibration, the
# admission filter run against the champion too, and the training/serving skew rate. Deterministic,
# aggregates only, and it FITS NOTHING — training happens in the engine's slow loop.
shadow-report:
	$(PYTHON) -m netcorenoc dataset shadow

# What capture currently costs, in rows. Zero-config means the default is good, not that the
# operator is blind about what the appliance is storing.
dataset-stats:
	$(PYTHON) -m netcorenoc dataset stats

# The corpus census (v0.11.0): what the promotion gate decides on real data, stated in advance.
# Beside `make eval` and the three dataset reports for their reason — a deterministic offline
# measurement over a frozen input, byte-identical across two runs and two processes. It carries its
# own CONTROL and EXITS NON-ZERO if the control comes back empty, because a census whose zero could
# be a property of the query rather than of the corpus is worse than no census.
census:
	$(PYTHON) tools/corpus_census.py

# Walk the audit hash chain and report the first broken link.
audit-verify:
	$(PYTHON) -m netcorenoc audit verify

# Local release-artifact build (no pipeline needed): sdist + wheel into ./dist.
dist:
	$(PYTHON) -m build
	@echo "built sdist + wheel in ./dist"

# Optional: also build the local Docker image (requires a running Docker daemon).
dist-image:
	docker build -t netcorenoc:local .

# Verify the version agrees across pyproject.toml, the package, and the CHANGELOG before tagging.
release-check:
	$(PYTHON) tools/release_check.py
