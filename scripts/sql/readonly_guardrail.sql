-- Strong DB-level guardrail for AI/query access.
-- Apply this script as superuser after loading schema/data.
--
-- Creates:
-- - role `db_readonly` (NOLOGIN)
-- - user `nl2sql_reader` (LOGIN) with least-privilege grants
-- - default read-only transactions for `nl2sql_reader`
--
-- IMPORTANT:
-- - Change the password before production use.
-- - Point application DATABASE_URL to `nl2sql_reader`.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db_readonly') THEN
        CREATE ROLE db_readonly NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl2sql_reader') THEN
        CREATE USER nl2sql_reader WITH PASSWORD 'change_me_now';
    END IF;
END
$$;

REVOKE ALL ON DATABASE dvdrental FROM db_readonly;
REVOKE ALL ON DATABASE dvdrental FROM nl2sql_reader;

GRANT CONNECT ON DATABASE dvdrental TO db_readonly;
GRANT USAGE ON SCHEMA public TO db_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO db_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO db_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO db_readonly;

GRANT db_readonly TO nl2sql_reader;

ALTER ROLE nl2sql_reader IN DATABASE dvdrental SET default_transaction_read_only = on;
ALTER ROLE nl2sql_reader SET statement_timeout = '15s';

COMMENT ON ROLE db_readonly IS 'Least-privilege role for read-only query workloads.';
COMMENT ON ROLE nl2sql_reader IS 'Read-only login user for NL2SQL agent.';
