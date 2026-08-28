# Security Policy

NetCoreNOC takes security seriously. This document is the **coordinated vulnerability disclosure
policy** — how to report a vulnerability and what to expect. If instead you are deploying or
operating NetCoreNOC, see the [operator security & operations guide](docs/security.md).

## Reporting a vulnerability

**Please report privately. Do not open a public issue, pull request, or discussion for a
security vulnerability.** A public report tells attackers before operators can patch.

Report through **either** private channel:

1. **GitHub private vulnerability reporting** (preferred): open a draft advisory at
   <https://github.com/leonardoSaaads/NetCoreNOC/security/advisories/new>. This is private
   between you and the maintainers and needs no additional setup.
2. **The maintainer's GitHub profile**: <https://github.com/leonardoSaaads> — contact the
   maintainer privately if you cannot use advisories.

Please include, as far as you can:

- the version or commit, and how NetCoreNOC was deployed (container, systemd, plain Python);
- a description of the issue and its impact;
- steps to reproduce, a proof of concept, or a failing test;
- any suggested remediation.

## Our commitment (response times)

- **Acknowledgement within 3 business days** of your report.
- **An initial assessment (validity + rough severity) within 10 business days.**
- **Regular updates** at least every 10 business days until the issue is resolved.
- A fix targeted **as soon as practical**, prioritised by severity. NetCoreNOC is a small,
  volunteer-maintained project — these are good-faith targets, not contractual SLAs.

## Coordinated disclosure and embargo

- We practise **coordinated disclosure**: please give us a reasonable chance to fix the issue
  before any public disclosure. A **90-day** embargo from acknowledgement is the default; we are
  happy to agree a different window with you, and to disclose sooner once a fix is released.
- We will credit you in the advisory and release notes unless you prefer to remain anonymous.
- If an issue is being actively exploited, we may accelerate the timeline.

## Scope

**In scope** — the NetCoreNOC codebase in this repository, including:

- the trap receiver and packet parsing (`src/netcorenoc/receiver.py`);
- the HTTP API, authentication, sessions, RBAC, and response shaping (`src/netcorenoc/api/` —
  **start at `api/perimeter.py`, which is the whole security boundary in one file** — plus
  `auth.py`, `rbac.py`, `shaping.py`);
- the audit log and its integrity guarantees (`src/netcorenoc/audit.py`);
- the SQLite storage layer and migrations (`src/netcorenoc/store/`, `migrations/`);
- the static UI and its CSP/security-header posture (`src/netcorenoc/ui/`);
- the shipped container image and the committed deployment artifacts
  (`Dockerfile`, `docker-compose.yml`, `deploy/`).

**Out of scope**:

- Vulnerabilities in third-party dependencies themselves — report those upstream (we do track
  them via `pip-audit` and will update our pins).
- Findings that require an already-compromised host, physical access, or a malicious operator
  account acting within its granted role (RBAC is enforced; abuse of a legitimately granted admin
  role is not a vulnerability).
- The unauthenticated nature of SNMPv2c/v1 trap intake itself — this is a protocol property
  handled by the source allowlist and documented as accepted residual risk in
  [`docs/security.md`](docs/security.md). A way to *bypass* the
  allowlist, or to inject content past the defensive parser, **is** in scope.
- Reports from automated scanners with no demonstrated impact, best-practice suggestions without
  a concrete vulnerability, and denial of service that merely requires flooding a trap listener
  the operator chose to expose (the bounded queue + `ingest_gap` accounting is the designed
  behaviour).

## Safe harbour

We support good-faith security research. If you make a good-faith effort to comply with this
policy, we will consider your research **authorised**, will not pursue or support legal action
against you for it, and will work with you to understand and resolve the issue. Good faith means:

- you only test against **your own** deployment (never a third party's), and never access, modify,
  or destroy data that is not yours;
- you avoid privacy violations, service degradation, and disruption to others;
- you give us a reasonable time to remediate before public disclosure (see the embargo above);
- you do not exploit the issue beyond the minimum needed to demonstrate it.

This is not an invitation to test infrastructure you do not own or operate.

## No bug bounty

NetCoreNOC does **not** operate a paid bug-bounty program and makes no offer of monetary reward.
We gratefully credit reporters in advisories and release notes.

## Where the security work is documented

- Threat model (STRIDE, per version): [`docs/security.md`](docs/security.md)
- Security reviews (finding → fix → test, standards mapping):
  [`docs/security.md`](docs/security.md) — `SECURITY-REVIEW-0.2.md`, `SECURITY-REVIEW-0.4.md`,
  `SECURITY-REVIEW-0.5.md`.
- Machine-readable contact (RFC 9116): served by the running app at `/.well-known/security.txt`;
  the committed source is
  [`src/netcorenoc/ui/.well-known/security.txt`](src/netcorenoc/ui/.well-known/security.txt).
