-- ADR #507: the Default customer, and a super admin whose access nobody can take away.
--
-- Every broker points to exactly one NOC customer, and the Default broker has always pointed to the
-- default organization. That organization is now a customer of the NOC like any other — the
-- **Default** customer, listed first, renamed by the owner as they please, never ended. Its old
-- name is replaced only where nobody has renamed it and no other organization holds "Default".
--
-- ADR #508: a person may be limited to brokers and to NOC customers — an admin too — so at least
-- one account must always reach everything. That is the **super admin** (`is_owner`): never
-- limited, never demoted, never disabled, never deleted. The database holds the first two of those
-- as a constraint, so no write path can make a super admin anything but an enabled admin. The first
-- enabled admin of an upgraded appliance becomes it; a fresh appliance's first admin is minted as it.

UPDATE organization SET name = 'Default'
 WHERE is_default AND name = 'Default organization'
   AND NOT EXISTS (SELECT 1 FROM organization o WHERE o.name = 'Default');

ALTER TABLE app_user ADD COLUMN is_owner boolean NOT NULL DEFAULT false;
ALTER TABLE app_user ADD CONSTRAINT app_user_owner_is_enabled_admin
    CHECK (NOT is_owner OR (role = 'admin' AND NOT disabled));

UPDATE app_user SET is_owner = true
 WHERE id = (SELECT id FROM app_user WHERE role = 'admin' AND NOT disabled ORDER BY id LIMIT 1);
