-- 0029: recovery by email (v0.30.0, ADR #445). Additive: one column, one table.
--
-- ## user.email
--
-- Where a person receives a password-reset link. Optional: NULL means "no recovery by email for
-- this account", which is every existing account until someone sets one, so an upgrade changes
-- nothing about how anyone signs in. Not unique — a team may share an on-call mailbox.
ALTER TABLE user ADD COLUMN email TEXT;

-- ## password_reset
--
-- One row per link sent. Only the SHA-256 of the link's token is stored, so a copy of the database
-- cannot be turned into a working link. A row is single-use (`used_at`), expires (`expires_at`,
-- 30 minutes after it was asked for), and is superseded by the next request for the same account;
-- rows past their expiry by a day are deleted whenever a new one is written, so the table stays as
-- small as the number of links currently in flight. Deleting the account deletes its rows.
CREATE TABLE password_reset (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL,
    used_at    REAL,
    source_ip  TEXT
);
CREATE INDEX idx_password_reset_user ON password_reset (user_id);
