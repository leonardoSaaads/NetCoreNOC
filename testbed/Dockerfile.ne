# One simulated network element for the testbed. NOT a product image.
#
# It carries `pysnmp` — already a runtime dependency of the appliance, so the lab adds **zero** new
# dependencies (prime directive 6) — plus the three files it actually runs: the agent, the scenario
# loader and the phase control, and `tools/trap_replay.py`, which is the PDU encoder the appliance's
# own corpus replay uses. Nothing from `src/netcorenoc/` is copied in, and that is deliberate rather
# than incidental: the lab is not allowed to import the product (prime directive 7), and an image
# that cannot see it cannot accidentally start.
#
# Same pinned base as the product image, so the lab does not drag a second Python into the build.
FROM python:3.12.8-slim

RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin labne

# pysnmp/pyasn1 only. No fastapi, no uvicorn, no aiosqlite: an NE is a sender.
RUN pip install --no-cache-dir "pysnmp>=7.1"

WORKDIR /lab
COPY tools/trap_replay.py /lab/tools/trap_replay.py
COPY testbed/ne /lab/ne
COPY testbed/scenarios /lab/scenarios

# `agent.py` resolves `tools/` as `<repo>/tools`, i.e. two levels up from `ne/`. Laying the image out
# with the same shape means the agent needs no container-specific path handling — the alternative is
# an `if` in the code for the benefit of one Dockerfile.
RUN mkdir -p /lab/state && chown -R labne /lab
USER labne
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NETCORENOC_TESTBED_STATE=/lab/state

ENTRYPOINT ["python", "/lab/ne/agent.py"]
