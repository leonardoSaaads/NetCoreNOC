-- 0044: each customer's equipment access and its mailbox (ADR #481). Forward-only and additive.
--
-- The owner's model: a case is opened, the agent identifies the customer whose equipment the
-- situation touches, reads its instructions and circuits, reaches its devices with the customer's
-- own credentials, and writes to the customer by email — and the customer's reply lands on the
-- case's feed and puts the case back in the queue. So the credentials and the mailbox live with
-- the customer, not in one vault-wide list an agent would have to cross-reference.
--
-- 1. `noc_secret.organization_id` — the customer a device credential belongs to. NULL for an AI
--    provider's key (the agents', `group = 'ai'`) and for a device credential stored before this
--    migration, until a custodian assigns it. It is not part of the seal's binding (host, port,
--    kind, version, access level are): moving a credential to another customer changes who reads
--    it in a list, not which machine it opens — and it is a custodian's stepped-up, audited act.
--
-- 2. `noc_customer_mail` — how the NOC writes to a customer and reads its replies: the recipients
--    of the customer update (`recipients`, `cc`), the sender, and two credentials of the
--    customer's own in the vault — an SMTP one to send through and an IMAP one whose inbox is read
--    for replies. Without an SMTP credential the appliance's own server (Settings → Email) sends.
--
-- 3. `noc_case_mail` — every message sent or read: to whom, its Message-ID (replies are matched
--    by it, or by the case tag in the subject), how it ended, and the feed post it became. An
--    inbound message from an address the customer has not registered is kept as `refused`, never
--    posted; one that names no case is `unmatched`. `(organization_id, direction, message_id)` is
--    unique, so a message read twice is posted once.
--
-- 4. A post's author may be the `customer` (a reply by email), and a case may come back to the
--    queue because the customer answered by email (`customer_email`).

ALTER TABLE noc_secret ADD COLUMN organization_id integer REFERENCES organization (id);
CREATE INDEX idx_noc_secret_customer ON noc_secret (organization_id, name, id);

CREATE TABLE noc_customer_mail (
    organization_id integer PRIMARY KEY REFERENCES organization (id),
    recipients      text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(recipients) <= 2000),
    cc              text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(cc) <= 2000),
    sender          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(sender) <= 254),
    sender_name     text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(sender_name) <= 120),
    smtp_secret_id  bigint REFERENCES noc_secret (id) ON DELETE SET NULL,
    imap_secret_id  bigint REFERENCES noc_secret (id) ON DELETE SET NULL,
    send_updates    boolean NOT NULL DEFAULT true,
    read_replies    boolean NOT NULL DEFAULT true,
    polled_at       timestamptz,
    poll_error      text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(poll_error) <= 500),
    version         integer NOT NULL DEFAULT 1,
    updated_at      timestamptz NOT NULL,
    updated_by      text COLLATE "C" NOT NULL
);

CREATE TABLE noc_case_mail (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organization (id),
    situation_id    bigint REFERENCES situation (id),
    direction       text COLLATE "C" NOT NULL CHECK (direction IN ('out', 'in')),
    outcome         text COLLATE "C" NOT NULL
                    CHECK (outcome IN ('sent', 'failed', 'posted', 'unmatched', 'refused')),
    message_id      text COLLATE "C" NOT NULL CHECK (length(message_id) BETWEEN 1 AND 998),
    subject         text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(subject) <= 998),
    sender          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(sender) <= 254),
    recipients      text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(recipients) <= 4000),
    post_id         bigint REFERENCES noc_case_post (id),
    detail          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(detail) <= 500),
    at              timestamptz NOT NULL,
    UNIQUE (organization_id, direction, message_id)
);
CREATE INDEX idx_noc_case_mail_case ON noc_case_mail (situation_id, at DESC);
CREATE INDEX idx_noc_case_mail_customer ON noc_case_mail (organization_id, at DESC);

ALTER TABLE noc_case_post DROP CONSTRAINT noc_case_post_author_kind_check;
ALTER TABLE noc_case_post ADD CONSTRAINT noc_case_post_author_kind_check
    CHECK (author_kind IN ('person', 'agent', 'system', 'customer'));

ALTER TABLE noc_case_cycle DROP CONSTRAINT noc_case_cycle_reason_check;
ALTER TABLE noc_case_cycle ADD CONSTRAINT noc_case_cycle_reason_check
    CHECK (reason IN ('opened', 'automation', 'customer_post', 'next_update', 'released',
                      'escalated', 'alarm', 'access', 'customer_email'));
