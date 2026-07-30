<!--
Thanks for contributing to NetCoreNOC! Please keep changes small and focused.
See CONTRIBUTING.md for the full quality bar. Do NOT report security vulnerabilities in a PR —
follow the coordinated disclosure policy in SECURITY.md.
-->

## What and why

<!-- What does this change do, and why? Link any related issue (Closes #NNN). -->

## Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Docs / process
- [ ] Refactor / internal (no behaviour change)

## Checklist

- [ ] `make qa` is green (ruff, mypy --strict, tests + coverage, `make eval` non-regression, dead-code gate)
- [ ] `make security` is green (bandit, pip-audit)
- [ ] Tests were added or updated for this change
- [ ] Docs and `CHANGELOG.md` updated where relevant
- [ ] **No new runtime dependency** (dev/CI tooling only, justified in `docs/adr/DECISIONS.md`)
- [ ] Commits follow Conventional Commits

## `make eval` delta (required if a scored path changed)

<!--
If you touched a scored path (receiver / correlate / learn / rootcause / severity /
varbind_profile / store ingest / engine), paste the `make eval` delta table here. A refactor that
intends no behaviour change must show a byte-identical delta; an intended metric change must be
explained. Delete this section if no scored path changed.
-->

## Notes for reviewers

<!-- Anything that helps review: trade-offs, follow-ups, screenshots for UI changes. -->
