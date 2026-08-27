"""The running appliance: the batch loop, and the periodic work off it.

`engine.py` is the queue, the batch and the one lock the whole thing turns on, and it is
permanently `COHESION_EXEMPT`: the ingest path has to be readable in one place. The rest
is what runs beside it — the attribute declaration site, maintenance, ingest gaps, and the
scoring seam's lifecycle.
"""
