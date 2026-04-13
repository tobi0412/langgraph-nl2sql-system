# Bootstrap DVD Rental

## Option A: quick local setup (included)

`docker-compose.yml` already initializes PostgreSQL with a minimal schema containing `film`, `actor`, and `rental` to validate Iteration 1.

## Option B: full official dataset (recommended for final demo)

1. Download the official dump:
   - [DVD Rental sample database](https://neon.com/postgresql/postgresql-getting-started/postgresql-sample-database)
2. Start Postgres:
   - `docker compose up -d postgres`
3. Restore dump (example with `dvdrental.tar` file):
   - `docker exec -i langgraph-nl2sql-postgres dropdb -U postgres --if-exists dvdrental`
   - `docker exec -i langgraph-nl2sql-postgres createdb -U postgres dvdrental`
   - `docker cp dvdrental.tar langgraph-nl2sql-postgres:/tmp/dvdrental.tar`
   - `docker exec -i langgraph-nl2sql-postgres pg_restore -U postgres -d dvdrental /tmp/dvdrental.tar`
4. Verify key tables:
   - `docker exec -i langgraph-nl2sql-postgres psql -U postgres -d dvdrental -c "\dt public.film public.actor public.rental"`
