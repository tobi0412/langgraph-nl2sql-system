# Demo Script - NL2SQL Multi-Agent (DVD Rental)

Guion reproducible de punta a punta exigido por `CONSIGNA.md` (seccion Deliverables).
Cubre los 4 escenarios obligatorios sobre la base **DVD Rental (pagila)**:

1. 1 sesion de documentacion de schema con correccion humana (HITL).
2. 3 consultas en lenguaje natural distintas (simple, regla de negocio, join canonico).
3. 1 refinamiento por follow-up sobre la misma sesion.

---

## 0. Prerrequisitos

### 0.1 Variables de entorno

```bash
cp .env.example .env
```

Completar al menos:

- `DATABASE_URL`: apuntando a la DVD Rental (ver 0.2).
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`: endpoint compatible con OpenAI Chat Completions.
- `SCHEMA_DOCS_PATH` (opcional, default `data/schema_docs.json`).

### 0.2 Carga del dataset DVD Rental

Opcion recomendada (dataset oficial completo): seguir `scripts/bootstrap_dvdrental.md`.

```bash
docker compose up -d postgres
docker exec -i langgraph-nl2sql-postgres dropdb -U postgres --if-exists dvdrental
docker exec -i langgraph-nl2sql-postgres createdb -U postgres dvdrental
docker cp data/dvdrental.tar langgraph-nl2sql-postgres:/tmp/dvdrental.tar
docker exec -i langgraph-nl2sql-postgres pg_restore -U postgres -d dvdrental /tmp/dvdrental.tar
docker exec -i langgraph-nl2sql-postgres psql -U postgres -d dvdrental -c "\dt public.film public.actor public.rental"
```

Salida esperada del ultimo comando: deben aparecer las tres tablas `public.actor`, `public.film`, `public.rental`.

### 0.3 Levantar el sistema

Todo el stack (Postgres + Tools HTTP + API + Streamlit):

```bash
docker compose up --build -d
```

- API FastAPI: <http://localhost:8000/health>
- Tools MCP HTTP: <http://localhost:8010/health>
- Streamlit (UI): <http://localhost:8501>

Checklist rapido antes de la demo:

- `GET /health` responde `200` con `database.ok=true`.
- `data/schema_docs.json` puede existir de una corrida previa; el script de
  demo usa `reset_schema=True` y regenera una version nueva.

---

## Demo end-to-end por terminal (sin UI)

Con los contenedores levantados, el script `scripts/demo.py` ejecuta los
4 escenarios obligatorios en una sola pasada, simulando la correccion humana
del Escenario 1 de forma programatica.

### Comando (host, PowerShell en Windows)

```powershell
$env:MCP_TOOLS_MODE="http"
$env:PYTHONIOENCODING="utf-8"
python scripts/demo.py
```

La override `MCP_TOOLS_MODE=http` hace que las tools se resuelvan via el
contenedor `tools:8010` (que tiene credenciales propias) en lugar de abrir
conexiones directas a Postgres con el usuario read-only local.

### Comando (bash/macos/linux)

```bash
MCP_TOOLS_MODE=http PYTHONIOENCODING=utf-8 python scripts/demo.py
```

### Comando (dentro del contenedor app)

```bash
docker exec -it langgraph-nl2sql-app python scripts/demo.py
```

### Que hace el script internamente

- **STEP 0**: `GET /health` contra `app:8000` y `tools:8010` con reporte de `database.ok`.
- **STEP 1**: corre el `SchemaAgentRunner` con `reset_schema=True`, espera el
  `interrupt` del nodo `human_gate`, edita en caliente las descripciones de
  `customer.activebool` y `film.rating`, y reanuda con `action="edit"` para
  persistir en `data/schema_docs.json` (version incrementada).
- **STEP 2**: lanza las 3 consultas NL de los sub-escenarios 2.1, 2.2 y 2.3
  mostrando nodo a nodo (`prepare -> planner -> critic -> execute -> finalize`),
  el SQL final, un sample de filas y la explicacion.
- **STEP 3**: crea una sesion nueva, tira un turno semilla y luego el follow-up
  "Y ahora filtrame solo los de la categoria Action", verificando al final que
  el SQL combine joins a `category`, filtro por Action y `SUM(amount)` heredado
  del turno anterior (short-term memory).
- **STEP 4**: resumen final con la version del schema y checklist de deliverables.

### Variables de entorno opcionales

- `DEMO_API_URL` (default `http://localhost:8000`).
- `DEMO_TOOLS_URL` (default se toma de `MCP_TOOLS_BASE_URL` o `http://localhost:8010`).
- `DEMO_LOG_LEVEL` (default `WARNING`; subir a `INFO` para ver logs de nodes/memoria).
- `MCP_TOOLS_MODE=http` obligatorio desde el host (ver explicacion arriba).

---

## 1. Escenario 1 - Documentacion de schema con correccion humana (HITL)

Objetivo: el **Schema Agent** introspecciona `public` via `mcp_schema_inspect`,
genera un borrador JSON con descripciones por tabla y columna, se detiene en el
nodo `human_gate` (`interrupt`) y espera feedback humano. La correccion se
persiste en `data/schema_docs.json` y queda disponible para el Query Agent.

### 1.1 Flujo por UI (Streamlit)

1. Abrir <http://localhost:8501> y seleccionar la tab **Schema Agent**.
2. (Opcional) Click en **Reset schema** para forzar una corrida limpia.
3. Click en **Documentar schema**.
   - Progreso: `agent -> tools (mcp_schema_inspect) -> agent -> format_draft -> human_gate`.
   - El panel principal renderiza el draft document (JSON + tabla por tabla).
4. En el panel de revision humana:
   - Tabla `customer`, columna `activebool`. Reemplazar descripcion por:

     ```text
     Business rule: un cliente se considera activo cuando activebool = true.
     Este campo es el filtro canonico para "clientes activos" en reportes.
     ```

   - Tabla `film`, columna `rating`:

     ```text
     Clasificacion MPAA (G, PG, PG-13, R, NC-17).
     ```

5. Elegir **Accion: edit** y apretar **Enviar feedback**.
6. El grafo retoma en `persist_approved` con `status: persisted` y `version` incrementada.

### 1.2 Flujo programatico (sin UI)

```python
from agents.schema_agent import SchemaAgentRunner

runner = SchemaAgentRunner()
session_id = "demo-schema-001"

result = runner.start(session_id=session_id, reset_schema=True)
draft = result["__interrupt__"][0].value["draft_document"]

for t in draft["tables"]:
    if t["table_name"] == "customer":
        for c in t["columns"]:
            if c["name"] == "activebool":
                c["description"] = (
                    "Business rule: un cliente se considera activo cuando "
                    "activebool = true. Filtro canonico para 'clientes activos'."
                )
    if t["table_name"] == "film":
        for c in t["columns"]:
            if c["name"] == "rating":
                c["description"] = "Clasificacion MPAA (G, PG, PG-13, R, NC-17)."

final = runner.resume(
    session_id=session_id,
    human_feedback={"action": "edit", "edited_document": draft},
)
print(final["status"])                      # 'persisted'
print(final["approved_document"]["version"])
```

### 1.3 Evidencia de persistencia

```bash
jq '.entries[-1] | {version, approved_at, tables: (.document.tables | length)}' data/schema_docs.json
```

Ejemplo de salida:

```json
{
  "version": 2,
  "approved_at": "2026-04-22T14:10:02.318411+00:00",
  "tables": 21
}
```

La descripcion editada para `customer.activebool` es la que dispara la regla
de negocio en el Escenario 2.2 (el planner del Query Agent deberia emitir
`WHERE activebool = true` aun cuando el usuario nunca mencione la columna).

---

## 2. Escenario 2 - Tres consultas en lenguaje natural

Prerrequisito: el schema quedo aprobado y persistido en el Escenario 1.
El Query Agent carga ese contexto (`prepare`), planifica (`planner`),
valida SQL (`critic`) y ejecuta read-only (`execute`).

Bloque Python equivalente para cada consulta:

```python
from agents.query_agent import QueryAgent

agent = QueryAgent()
session_id = "demo-query-001"
user_id = "demo-user"

resp = agent.run(
    "<PREGUNTA>",
    session_id=session_id,
    user_id=user_id,
)
print(resp["status"])            # ok | needs_clarification | blocked_missing_schema
print(resp["sql_final"])
print(resp["sample"])
print(resp["explanation"])
```

### 2.1 Consulta 1 - Simple (top-N sobre una tabla base)

Prompt:

```text
Mostrame los 10 films con mayor duracion
```

Comportamiento esperado:

- `status = ok`.
- `planner.candidate_tables` incluye `film`.
- `planner.minimum_viable_schema` incluye `film`.
- El SQL usa la tabla base `film` (no las vistas `film_list` o
  `nicer_but_slower_film_list`), con `ORDER BY ... DESC LIMIT 10`.

SQL representativo:

```sql
SELECT film_id, title, length
FROM film
ORDER BY length DESC
LIMIT 10;
```

`sample` esperado: 10 filas, cada una con `length` entre 180 y 185 minutos.

### 2.2 Consulta 2 - Regla de negocio

Prompt:

```text
Cuantos clientes activos hay?
```

Comportamiento esperado:

- `status = ok`.
- SQL incluye `activebool` (gracias al schema corregido en 1.3).
- SQL no cae en la vista pre-agregada `customer_list`.

SQL esperado:

```sql
SELECT COUNT(*) AS active_customers
FROM customer
WHERE activebool = true;
```

`sample` esperado: una unica fila con un entero cercano a 584 en DVD Rental oficial.

Esta es la prueba clave de que el Escenario 1 (HITL + persistencia) tuvo
efecto: si no hubieramos editado la descripcion, el modelo tenderia a
generar `customer_list` u otra variante.

### 2.3 Consulta 3 - Join canonico multi-tabla

Prompt:

```text
Ingresos totales por categoria de pelicula
```

Comportamiento esperado:

- `status = ok`.
- Recorre el camino canonico: `payment -> rental -> inventory -> film -> film_category -> category`.
- No usa la vista `sales_by_film_category` (el usuario no la pidio explicitamente).
- `SUM(...)` + `GROUP BY category.name`.

SQL esperado:

```sql
SELECT c.name AS category,
       SUM(p.amount) AS total_revenue
FROM payment p
JOIN rental r          ON r.rental_id       = p.rental_id
JOIN inventory i       ON i.inventory_id    = r.inventory_id
JOIN film_category fc  ON fc.film_id        = i.film_id
JOIN category c        ON c.category_id     = fc.category_id
GROUP BY c.name
ORDER BY total_revenue DESC;
```

`sample` esperado: 16 categorias con `total_revenue` entre ~4.000 y ~4.500 USD.

---

## 3. Escenario 3 - Follow-up / refinamiento iterativo

Requisito: reusar el mismo `session_id` para que la memoria de corto plazo
(`memory/session_store.py`) exponga al planner el `last_sql` y las
`recent_filters` de los turnos previos.

### 3.1 Turno semilla

```text
Dame los 5 films con mas ingresos
```

Produce un SQL con `SUM(payment.amount)`, joins
`payment -> rental -> inventory -> film`, `ORDER BY total DESC LIMIT 5`.

### 3.2 Turno follow-up

Prompt:

```text
Y ahora filtrame solo los de la categoria Action
```

Comportamiento esperado:

- El planner reconoce el pronombre "los" leyendo `session.last_sql`.
- Extiende la query anterior sin empezar de cero:
  - mantiene `SUM(payment.amount)`, joins y `LIMIT 5`.
  - suma joins a `film_category` y `category`.
  - agrega filtro `WHERE LOWER(category.name) = 'action'`.
- `planner.minimum_viable_schema` incluye `film` y `category`.

SQL esperado:

```sql
SELECT f.title,
       SUM(p.amount) AS total_revenue
FROM payment p
JOIN rental r         ON r.rental_id    = p.rental_id
JOIN inventory i      ON i.inventory_id = r.inventory_id
JOIN film f           ON f.film_id      = i.film_id
JOIN film_category fc ON fc.film_id     = f.film_id
JOIN category c       ON c.category_id  = fc.category_id
WHERE LOWER(c.name) = 'action'
GROUP BY f.title
ORDER BY total_revenue DESC
LIMIT 5;
```

`sample`: 5 filas, todas de categoria Action, ordenadas por revenue.

### 3.3 Evidencia de la memoria de corto plazo

Despues del follow-up, la sesion en disco (`data/session_memory.json`)
contiene, bajo el `session_id` usado:

```json
{
  "last_question": "Y ahora filtrame solo los de la categoria Action",
  "last_sql": "SELECT f.title, SUM(p.amount) ... LOWER(c.name) = 'action' ...",
  "recent_filters": ["LOWER(c.name) = 'action'"],
  "working_messages": [
    {"role": "user", "content": "Dame los 5 films con mas ingresos"},
    {"role": "assistant", "content": "SQL: SELECT f.title, SUM(p.amount) ..."},
    {"role": "user", "content": "Y ahora filtrame solo los de la categoria Action"},
    {"role": "assistant", "content": "SQL: ... WHERE LOWER(c.name) = 'action' ..."}
  ]
}
```

Eso cierra el loop: memoria persistente (preferencias) + memoria de corto plazo
(hilo conversacional) + schema aprobado por humano + validacion read-only,
todo orquestado por el mismo LangGraph.

---

## 4. Troubleshooting rapido

| Sintoma | Causa probable | Fix |
|---|---|---|
| `blocked_missing_schema` en Query Agent | No se aprobo el schema en 1 | Ejecutar el Escenario 1 completo (o `scripts/demo.py`) |
| Validador rechaza el SQL con `disallowed_statement` | El LLM genero `UPDATE`/`DELETE` | Se corta en `critic`, no se ejecuta; reintentar o reformular |
| `runtime_error` en `execute` sobre DVD Rental | Dataset incompleto | Correr el bootstrap de 0.2 con el `.tar` oficial |
| `connection failed ... nl2sql_reader` al correr desde host | Tools en modo `local` intentando conectar con las credenciales del `.env` | Exportar `MCP_TOOLS_MODE=http` antes de ejecutar |
| `needs_clarification` en una pregunta que no deberia serlo | Prompt demasiado vago, memoria de corto plazo limpia | Reformular o sembrar un turno previo (ver 3.1) |
| Cambios en `schema_docs.json` no se reflejan en Query Agent | Cache de corrida previa | Rehacer el Escenario 1 o borrar `data/schema_docs.json` |
