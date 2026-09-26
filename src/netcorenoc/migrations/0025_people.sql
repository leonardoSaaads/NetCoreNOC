-- 0025: people — a display name, a profile photo, and a purpose for a service token (v0.25.0).
--
-- ## user.display_name (ADR #401)
--
-- The username is an identifier: it signs in, it is unique, and audit rows name it. What the
-- console shows beside a face is a person's name, which may change and need not be unique. NULL
-- means "show the username", so every existing account reads exactly as before.
ALTER TABLE user ADD COLUMN display_name TEXT;

-- ## user_avatar (ADR #402)
--
-- One small image per account, in a table of its own so that listing users never reads an image:
-- `list_users` joins the digest only. The browser re-encodes the photo before it is sent (a square
-- of at most 320 px, WebP or PNG), the server re-checks the bytes it received — the format from its
-- magic number, the dimensions from its header, at most 64 KiB — and serves them under a URL that
-- carries this digest, so a browser caches each photo once and never asks again until it changes.
-- Deleting the account deletes the photo.
CREATE TABLE user_avatar (
    user_id    INTEGER PRIMARY KEY REFERENCES user (id) ON DELETE CASCADE,
    mime       TEXT    NOT NULL,               -- image/png | image/webp | image/jpeg
    image      BLOB    NOT NULL,
    sha256     TEXT    NOT NULL,               -- hex digest of `image`: the ETag and the URL version
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    updated_at REAL    NOT NULL
);

-- ## api_token.purpose (ADR #404)
--
-- A token is named for the identity it acts as ("grafana", "ci-export"); the purpose says what it is
-- for, so a list of tokens can be read a year later by someone who did not create them.
ALTER TABLE api_token ADD COLUMN purpose TEXT;
