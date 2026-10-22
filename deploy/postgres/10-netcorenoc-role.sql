-- The appliance's roles in the bundled database (docker-compose.yml, ADRs #452, #453).
--
-- Run once by the postgres image, when its data directory is first initialised, as the cluster's
-- superuser (`postgres`). Least privilege:
--
--   netcorenoc      owns the database, so it may create the schema; `db-migrate` runs as it.
--   netcorenoc_app  may connect, and nothing else until `db-migrate` grants it rows: the appliance
--                   runs as it, and may not alter, drop or truncate anything.
--
-- Neither is a superuser or may create databases or roles. psql reads the passwords from the
-- generated files itself, so they are on no command line.
\set ON_ERROR_STOP on
\set owner_password `cat /run/netcorenoc-secrets/db_password`
\set app_password `cat /run/netcorenoc-secrets/db_app_password`
CREATE ROLE netcorenoc LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'owner_password';
CREATE ROLE netcorenoc_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'app_password';
ALTER DATABASE netcorenoc OWNER TO netcorenoc;
REVOKE ALL ON DATABASE netcorenoc FROM PUBLIC;
GRANT CONNECT ON DATABASE netcorenoc TO netcorenoc_app;
