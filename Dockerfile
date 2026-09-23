# CIS-style hardened image (§A.6). Multi-stage: no build tools in the final image; non-root;
# the only writable path is the DB directory, so the container runs fine read-only (see below).
#
# Pin the base image by digest in production for a reproducible, tamper-evident build:
#   docker pull python:3.12.8-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12.8-slim
# then replace the tag below with  python@sha256:<digest>.  A specific patch tag is pinned here
# (not floating 3.12-slim) so the shipped default is already reproducible per patch release.
FROM python:3.12.8-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12.8-slim
# The IANA time-zone database (v0.21.0, D2). **An operating-system package, not a Python runtime
# dependency** — `pyproject.toml` is unchanged and `zoneinfo` is in the standard library; what it
# lacks on a slim image is the *data*, which every Linux distribution ships as `tzdata`. Prime
# directive 8 says no new Python runtime dependency, and this is not one.
#
# Without it `ZoneInfo("America/Sao_Paulo")` raises and a maintenance window cannot be scheduled in
# any zone at all. The appliance does not fail silently either way: `timezone_selfcheck()` runs at
# startup against the database *this image actually has* and raises an operator warning naming what
# it could not resolve — because a zone list validated on the build machine is the trap
# `docs/findings.md` F131 records one layer up, where `docker compose config` never read
# `.dockerignore` and the testbed shipped unbuildable.
#
# `tzdata-legacy` is deliberately NOT installed. It carries the backward links — `PRC`, `ROC`,
# `ROK`, `UCT` — which are deprecated aliases; every entry in the curated list is a canonical zone,
# so the appliance never needs one.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin netcorenoc
COPY --from=build /install /usr/local
USER netcorenoc
WORKDIR /home/netcorenoc
ENV NETCORENOC_DB=/home/netcorenoc/netcorenoc.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# SNMP trap listener (UDP) and web UI/API.
EXPOSE 162/udp 8080
# Recommended hardened run (documented in SECURITY.md): drop all capabilities, forbid privilege
# escalation, read-only root filesystem with the DB on a writable volume:
#   docker run --read-only --cap-drop ALL --security-opt no-new-privileges \
#     --tmpfs /tmp -v netcorenoc-data:/home/netcorenoc -p 162:162/udp -p 8080:8080 netcorenoc
CMD ["python", "-m", "netcorenoc.main"]
