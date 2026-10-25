"""The NOC plane (phase 2): what the appliance does about a situation once it has one (ADR #454).

Phase 1 — the broker — turns traps into explained situations. The NOC plane is where those
situations are worked: by people, and by AI agents that reach equipment through credentials they
are lent and never shown. This domain begins with the credential vault:

* `kinds` — what a device credential is: its kinds, fields, access levels and binding;
* `health` — the status light, from the history of reachability checks;
* `probe` — the checks themselves, off the ingest path and outside ``store.lock``;
* `lease` — the signed, short-lived lease an agent presents instead of a credential.

It imports `crosscutting` and the store, and nothing in the other six domains: the broker does not
know the NOC exists, and nothing here can reach the trap path.
"""
