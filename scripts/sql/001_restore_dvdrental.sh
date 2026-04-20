#!/usr/bin/env bash
# Restaura el dump oficial de dvdrental (formato custom/tar de pg_dump)
# sobre la base `dvdrental` que el entrypoint de postgres ya creo vacia.
#
# El archivo se monta como read-only en /dvdrental.tar desde ./data/dvdrental.tar

set -euo pipefail

DUMP_FILE="/dvdrental.tar"
TARGET_DB="${POSTGRES_DB:-dvdrental}"

if [ ! -f "${DUMP_FILE}" ]; then
  echo "[restore_dvdrental] ERROR: no se encontro ${DUMP_FILE}. ¿Esta montado ./data/dvdrental.tar?" >&2
  exit 1
fi

echo "[restore_dvdrental] Restaurando ${DUMP_FILE} -> ${TARGET_DB} ..."

# --no-owner / --no-privileges: el dump referencia usuarios que no existen aca.
# --exit-on-error: fallar el init si algo sale mal (evita quedar con DB incompleta).
pg_restore \
  --username "${POSTGRES_USER}" \
  --dbname "${TARGET_DB}" \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  --verbose \
  "${DUMP_FILE}"

echo "[restore_dvdrental] Restore completado."
