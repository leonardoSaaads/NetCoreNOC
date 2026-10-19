-- The appliance's role in the bundled database (docker-compose.yml, ADR #452): least privilege.
--
-- Run once by the postgres image, when its data directory is first initialised, as the cluster's
-- superuser (`postgres`). The role logs in and owns its database, so it may create its schema; it
-- is not a superuser and may not create databases or roles. psql reads the password from the
-- generated file itself, so it is on no command line.
\set ON_ERROR_STOP on
\set app_password `cat /run/netcorenoc-secrets/db_password`
CREATE ROLE netcorenoc LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'app_password';
ALTER DATABASE netcorenoc OWNER TO netcorenoc;
REVOKE ALL ON DATABASE netcorenoc FROM PUBLIC;
