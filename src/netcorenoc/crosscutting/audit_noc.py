"""The NOC plane's audit actions — part of the frozen catalog `audit.ACTIONS` (ADRs #455-#461).

Split from `audit.py` at the 400-line guard, on the seam the product has: the broker's actions
there, the NOC's here. `audit.ACTIONS` is the union and stays the one authority; nothing else reads
this name.
"""

from __future__ import annotations

NOC_ACTIONS: frozenset[str] = frozenset(
    {
        # The credential vault (ADRs #455-#458). One action per act an auditor asks about
        # separately — who created, changed, deleted or READ a credential, who let agents use it,
        # who named a custodian or an agent, and which agent leased, used or let go of one. A
        # failed step-up is the attempted act with outcome `denied`; `noc.secret.read` is written
        # only as a denial. No row ever holds a secret value or a lease.
        "noc.secret.create",
        "noc.secret.update",
        "noc.secret.delete",
        "noc.secret.reveal",
        "noc.secret.read",
        "noc.secret.agents",
        "noc.grant.change",
        "noc.lease.issue",
        "noc.lease.use",
        "noc.lease.revoke",
        # Cases (ADRs #459-#461): who took, released, edited, set pending or resolved a case, and
        # who posted on its feed — under the actor's name, person or agent. The case loop's own
        # moves are events in the case feed, not audit rows: nobody chose them.
        "noc.case.take",
        "noc.case.release",
        "noc.case.update",
        "noc.case.status",
        "noc.case.resolve",
        "noc.case.post",
    }
)
