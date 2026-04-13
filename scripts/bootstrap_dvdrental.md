# Bootstrap DVD Rental

## Opcion A: entorno local rapido (incluido)

`docker-compose.yml` ya inicializa PostgreSQL con un esquema minimo que contiene `film`, `actor` y `rental` para validar la Iteracion 1.

## Opcion B: dataset oficial completo (recomendado para demo final)

1. Descargar el dump oficial:
   - [DVD Rental sample database](https://neon.com/postgresql/postgresql-getting-started/postgresql-sample-database)
2. Levantar Postgres:
   - `docker compose up -d postgres`
3. Restaurar dump (ejemplo con archivo `dvdrental.tar`):
   - `docker exec -i langgraph-nl2sql-postgres dropdb -U postgres --if-exists dvdrental`
   - `docker exec -i langgraph-nl2sql-postgres createdb -U postgres dvdrental`
   - `docker cp dvdrental.tar langgraph-nl2sql-postgres:/tmp/dvdrental.tar`
   - `docker exec -i langgraph-nl2sql-postgres pg_restore -U postgres -d dvdrental /tmp/dvdrental.tar`
4. Verificar tablas clave:
   - `docker exec -i langgraph-nl2sql-postgres psql -U postgres -d dvdrental -c "\dt public.film public.actor public.rental"`
