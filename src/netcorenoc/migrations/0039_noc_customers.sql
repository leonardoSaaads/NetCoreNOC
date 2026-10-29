-- 0039: the NOC's customers — each one's instructions, workbook, documents and circuits (ADR #473).
-- Forward-only and additive.
--
-- A customer is an `organization` (every one but the default): the companies whose equipment the
-- NOC watches — an Akamai, a TIM Brasil. What the NOC knows about each, and what its agents read
-- over MCP before they act, is four things:
--
-- 1. `noc_customer` — its **instructions** (how it wants the NOC to act: how a case is named, when
--    to call, what never to touch), kept by people; and its **memory** (what the NOC learned working
--    its network: the port that flaps, the provider that answers at night), which agents keep
--    current as they work. Created on the first write; a customer with neither reads as empty.
--
-- 2. `noc_contact` — its **workbook**: who to call, by company (`party`: the customer itself, a
--    fibre provider such as Zayo, a vendor) and in which order (`escalation_level`, 1 first).
--
-- 3. `noc_customer_doc` — the **documents** it deposits for troubleshooting (topology, shelf
--    layouts, distances, rack photos): the file as it was handed over, checked by its first bytes
--    (`engine/noc/doctext.py`), and the text an agent can read from it.
--
-- 4. `noc_circuit` — its **circuits**: each one's designation, ends, bandwidth, owner and provider,
--    and its path hop by hop (client port, line port, ODF to ODF, patch cords, the far client port),
--    as `path`, a JSON list of hops. `search` is the lower-case text a circuit is found by — a port,
--    an ODF position, a provider's reference — so an agent with an alarm on a port finds it.

CREATE TABLE noc_customer (
    organization_id integer PRIMARY KEY REFERENCES organization (id),
    instructions    text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(instructions) <= 65536),
    memory          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(memory) <= 32768),
    version         integer NOT NULL DEFAULT 1,
    updated_at      timestamptz NOT NULL,
    updated_by      text COLLATE "C" NOT NULL,
    memory_at       timestamptz,
    memory_by       text COLLATE "C"
);

CREATE TABLE noc_contact (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  integer NOT NULL REFERENCES organization (id),
    party            text COLLATE "C" NOT NULL CHECK (length(party) BETWEEN 1 AND 120),
    party_kind       text COLLATE "C" NOT NULL
                     CHECK (party_kind IN ('customer', 'provider', 'vendor', 'other')),
    escalation_level smallint NOT NULL DEFAULT 1 CHECK (escalation_level BETWEEN 1 AND 9),
    name             text COLLATE "C" NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    role             text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(role) <= 120),
    email            text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(email) <= 254),
    phone            text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(phone) <= 60),
    hours            text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(hours) <= 120),
    notes            text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    version          integer NOT NULL DEFAULT 1,
    created_at       timestamptz NOT NULL,
    created_by       text COLLATE "C" NOT NULL,
    updated_at       timestamptz NOT NULL,
    updated_by       text COLLATE "C" NOT NULL
);
CREATE INDEX idx_noc_contact_customer ON noc_contact (organization_id, party, escalation_level);

CREATE TABLE noc_customer_doc (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organization (id),
    name            text COLLATE "C" NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    title           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(title) <= 200),
    category        text COLLATE "C" NOT NULL DEFAULT 'other'
                    CHECK (category IN ('topology', 'site', 'equipment', 'procedure', 'other')),
    ext             text COLLATE "C" NOT NULL CHECK (length(ext) BETWEEN 1 AND 8),
    mime            text COLLATE "C" NOT NULL CHECK (length(mime) <= 120),
    size            integer NOT NULL CHECK (size BETWEEN 1 AND 12582912),
    sha256          text COLLATE "C" NOT NULL CHECK (length(sha256) = 64),
    content         bytea NOT NULL,
    body_text       text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(body_text) <= 1000000),
    text_note       text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(text_note) <= 200),
    created_at      timestamptz NOT NULL,
    created_by      text COLLATE "C" NOT NULL
);
CREATE INDEX idx_noc_doc_customer ON noc_customer_doc (organization_id, category, id);

CREATE TABLE noc_circuit (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id integer NOT NULL REFERENCES organization (id),
    designation     text COLLATE "C" NOT NULL CHECK (length(designation) BETWEEN 1 AND 200),
    a_end           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(a_end) <= 120),
    z_end           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(z_end) <= 120),
    bandwidth       text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(bandwidth) <= 40),
    service         text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(service) <= 80),
    status          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(status) <= 40),
    owner           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(owner) <= 120),
    provider        text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(provider) <= 120),
    rfs_date        text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(rfs_date) <= 40),
    cease_date      text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(cease_date) <= 40),
    attributes      text COLLATE "C" NOT NULL DEFAULT '{}' CHECK (length(attributes) <= 40000),
    path            text COLLATE "C" NOT NULL DEFAULT '[]' CHECK (length(path) <= 60000),
    notes           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(notes) <= 8000),
    search          text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(search) <= 20000),
    source          text COLLATE "C" NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('manual', 'import')),
    version         integer NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL,
    created_by      text COLLATE "C" NOT NULL,
    updated_at      timestamptz NOT NULL,
    updated_by      text COLLATE "C" NOT NULL,
    notes_at        timestamptz,
    notes_by        text COLLATE "C",
    UNIQUE (organization_id, designation)
);
