"""The Default customer's example book, written once on a fresh appliance (ADR #507).

Every broker points to one NOC customer, and the Default broker to the **Default** customer from
the first boot. Its book starts with one example of each part — instructions, a work contact, an
additional document and a circuit — so a new owner sees what each part is for and edits or deletes
it, instead of meeting four empty tabs. Written only when that book is wholly empty, by
``system:example``, so an upgraded appliance (or a book someone already filled) is never touched.
"""

from __future__ import annotations

import hashlib
from typing import Any

from netcorenoc.crosscutting import administration
from netcorenoc.engine.noc import circuits, customers

ACTOR = "system:example"

INSTRUCTIONS = """\
Example instructions — replace them with how this customer wants to be served.

1. Name a case "<site> · <what failed>", for example "POP Centro · uplink down".
2. Read the Work Contact first; call level 1 before level 2.
3. Never reboot core equipment without the customer's written approval in the case.
4. Planned work is announced on the Maintenance board; a fault inside a window is not a case.
5. Send the customer an update every 30 minutes while a case is open.
"""

CONTACT: dict[str, Any] = {
    "party": "Default (example)",
    "party_kind": "customer",
    "escalation_level": 1,
    "name": "NOC hotline (example)",
    "role": "First call, 24x7",
    "email": "noc@example.com",
    "phone": "+55 11 4000-0000",
    "hours": "24x7",
    "notes": "An example contact: change it to the customer's real NOC, or delete it.",
}

DOC_NAME = "example-escalation-guide.txt"
DOC_TITLE = "Escalation guide (example)"
DOC_TEXT = """\
Escalation guide — an example document. Replace or delete it.

Agents read the text of every document here before acting on this customer's network.
Put here what a troubleshooter needs and the customer handed over: topology, site lists,
equipment notes, procedures.

Severity 1 (service down): call level 1 at once, then level 2 after 15 minutes without an answer.
Severity 2 (degraded): email level 1 and follow up within one hour.
Severity 3 (no impact): email level 1 within one business day.
"""

CIRCUIT: dict[str, Any] = {
    "designation": "EXAMPLE-0001",
    "a_end": "POP Centro",
    "z_end": "Customer HQ",
    "bandwidth": "1 Gbps",
    "service": "Ethernet",
    "status": "Active",
    "owner": "Default (example)",
    "provider": "Example Carrier",
    "path": [
        {"site": "POP Centro", "segment": "Access", "fields": {"port": "ge-0/0/1", "vlan": "100"}},
        {"site": "Customer HQ", "segment": "CPE", "fields": {"port": "ge-0/0/0"}},
    ],
    "notes": "An example circuit. Clone it to add the next one quickly, or delete it.",
}


async def seed_default_customer(store: Any, now: float) -> bool:
    """Write the example book into the Default customer when its book is empty; True if written.

    The caller commits: this runs inside the first boot's own transaction, beside the first admin.
    """
    organization_id = int(await store.default_organization_id())
    book = await store.noc_customer(organization_id)
    if book is None or book["instructions"] or book["memory"] or book["circuits"]:
        return False
    if (
        await store.noc_count_contacts(organization_id)
        or (await store.noc_docs_usage(organization_id))[0]
    ):
        return False
    await store.noc_set_instructions(organization_id, INSTRUCTIONS, now, ACTOR)
    await store.noc_create_contact(organization_id, customers.contact(CONTACT), now, ACTOR)
    data = DOC_TEXT.encode("utf-8")
    await store.noc_create_doc(
        organization_id,
        {
            "name": DOC_NAME,
            "title": DOC_TITLE,
            "category": "procedure",
            "ext": "txt",
            "mime": "text/plain",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": data,
            "body_text": DOC_TEXT,
            "text_note": "",
        },
        now,
        ACTOR,
    )
    circuit = circuits.clean(CIRCUIT)
    await store.noc_put_circuit(
        organization_id, circuit, circuits.search_text(circuit), "manual", now, ACTOR
    )
    return True


async def first_boot(store: Any, now: float) -> tuple[administration.Bootstrap | None, int]:
    """The admin minted when the appliance has none (#234), and how many accounts there were.

    `existing_users` is read BEFORE the bootstrap, so the banner can tell a first run from a
    recovery: after it, both look identical — an admin exists either way. On a first run the
    Default customer's book gets its examples, beside the first admin. The caller commits.
    """
    existing_users = int(await store.count_users())
    minted = await administration.bootstrap_admin(store, now)
    if minted is not None and existing_users == 0:
        await seed_default_customer(store, now)
    return minted, existing_users
