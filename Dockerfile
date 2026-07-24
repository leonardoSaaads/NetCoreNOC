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
