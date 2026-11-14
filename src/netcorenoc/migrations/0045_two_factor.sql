-- 0045: two-factor sign-in for every person — an authenticator code after the password (ADR #482).
-- Forward-only.
--
-- A password alone was every account's only factor (ADR #456 left a placeholder for the second).
-- From 0045 a person signs in in two steps: the password, then a six-digit code from an
-- authenticator app (TOTP, RFC 6238). Whoever has no authenticator yet enrols one at that second
-- step — a QR code, ten recovery codes, and the first code to prove the app reads it — and only
-- then is a session opened. Service tokens are untouched: they are not people.
--
-- * `user_second_factor` — one row per enrolled person: the shared secret, sealed under the
--   appliance's key (never in clear), and the last 30-second step a code was accepted for, so a
--   code cannot be used twice.
-- * `user_recovery_code` — the single-use recovery codes, as keyed digests (never the codes).
-- * `login_challenge` — the half-way point between the two steps: who passed the password, for
--   what (`verify` an enrolled account, or `enroll` one), until when, and how many codes were
--   tried. An enrolment carries the secret it offered, sealed, and the digests of the recovery
--   codes it showed. Only a digest of the challenge's handle is stored.
--
-- Every session open now was opened by a password alone, so they all end here: each person signs
-- in again, with the second factor.

CREATE TABLE user_second_factor (
    user_id     integer     PRIMARY KEY REFERENCES app_user (id) ON DELETE CASCADE,
    secret      text COLLATE "C" NOT NULL,
    enrolled_at timestamptz NOT NULL,
    last_step   bigint      NOT NULL DEFAULT 0,
    last_used_at timestamptz
);

CREATE TABLE user_recovery_code (
    user_id    integer     NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    digest     text COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    used_at    timestamptz,
    PRIMARY KEY (user_id, digest)
);

CREATE TABLE login_challenge (
    challenge_hash text COLLATE "C" PRIMARY KEY,
    user_id        integer     NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    purpose        text COLLATE "C" NOT NULL CHECK (purpose IN ('verify', 'enroll')),
    secret         text COLLATE "C",
    recovery       text COLLATE "C",
    created_at     timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,
    attempts       integer     NOT NULL DEFAULT 0,
    source_ip      text COLLATE "C",
    CHECK ((purpose = 'enroll') = (secret IS NOT NULL))
);
CREATE INDEX idx_login_challenge_user ON login_challenge (user_id);
CREATE INDEX idx_login_challenge_expiry ON login_challenge (expires_at);

DELETE FROM session;
