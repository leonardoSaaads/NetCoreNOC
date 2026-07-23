# OptiCorr build targets. Override PYTHON to use another interpreter, e.g. in CI:
#   make qa PYTHON=python
PYTHON ?= .venv/bin/python

.PHONY: qa lint typecheck test security run replay loadtest burst fmt migrate audit-verify \
	eval eval-baseline corpus

qa: lint typecheck test eval

security:
	$(PYTHON) -m bandit -q -c pyproject.toml -r opticorr tools
	$(PYTHON) -m pip_audit

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov=opticorr --cov-report=term-missing

fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

run:
	$(PYTHON) -m opticorr.main

# Replay the fiber-cut fixture against a locally running OptiCorr (port 1162 by default
# so it works without privileges; export OPTICORR_TRAP_PORT=1162 for the server too).
replay:
	$(PYTHON) tools/trap_replay.py tests/fixtures/fiber_cut.json --port $${OPTICORR_TRAP_PORT:-1162}

# 1000 traps/s for 60 s against a locally running OptiCorr.
loadtest:
	$(PYTHON) tools/trap_replay.py --synthetic 20 --classes 10 --rate 1000 --duration 60 \
		--port $${OPTICORR_TRAP_PORT:-1162}

# A 100 000-trap burst against a locally running OptiCorr (the v0.3.0 window/backpressure
# guard; the pass/fail assertion lives in tests/test_perf.py::burst).
burst:
	$(PYTHON) tools/trap_replay.py --synthetic 50 --classes 20 --rate 100000 --duration 1 \
		--port $${OPTICORR_TRAP_PORT:-1162}

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

# Apply pending schema migrations to OPTICORR_DB (idempotent; runs at startup too).
migrate:
	$(PYTHON) -c "import asyncio, os; from opticorr.store import Store; \
		s=Store(os.environ.get('OPTICORR_DB','opticorr.db')); \
		asyncio.run(s.open()); asyncio.run(s.close()); print('migrations applied')"

# Walk the audit hash chain and report the first broken link.
audit-verify:
	$(PYTHON) -m opticorr audit verify
