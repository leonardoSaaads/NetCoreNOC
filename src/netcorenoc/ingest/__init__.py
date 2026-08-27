"""Ingest: the wire.

Parse, allowlist, quarantine, and the trap vocabulary. The bottom of the stack: nothing
here imports another layer, which is what would let this subtree run somewhere else —
the proxy/agent question DECISIONS #216 names and does not answer.
"""
