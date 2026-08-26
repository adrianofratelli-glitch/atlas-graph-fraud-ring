#!/bin/bash
# Pipeline completo de dados, na ordem correta. Idempotente de ponta a ponta.
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BASE/.venv/bin/python"
PEOPLE="${PEOPLE:-150000}"
TXNS="${TXNS:-600000}"
RINGS="${RINGS:-40}"

echo "▶ 1/4 população sintética ($PEOPLE pessoas, $TXNS transações)"
"$PY" "$BASE/data-generator/generate_synthetic_data.py" --people "$PEOPLE" --transactions "$TXNS" "$@"

echo "▶ 2/4 índices B-tree"
mongosh "$(grep '^MONGODB_URI=' "$BASE/.env" | cut -d= -f2-)" --quiet "$BASE/schema/indexes.js"

echo "▶ 3/4 redes de fraude ($RINGS redes)"
"$PY" "$BASE/data-generator/inject_fraud_rings.py" --rings "$RINGS" --ring-size-min 20 --ring-size-max 30

echo "▶ 4/4 materialização de arestas (recria os índices de `connections`)"
"$PY" "$BASE/data-generator/materialize_connections.py" --rebuild

echo "▶ conferindo os índices de traversal"
mongosh "$(grep '^MONGODB_URI=' "$BASE/.env" | cut -d= -f2-)" --quiet --eval '
db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");
const nomes = db.connections.getIndexes().map(i => i.name);
for (const req of ["from_1", "to_1"]) {
  if (!nomes.includes(req)) { print("FALTA o índice " + req + " em connections"); quit(1); }
}
print("  ok: " + nomes.join(", "));'"

echo "✅ dados prontos. Próximo: schema/search_indexes.py e data-generator/embed_reasons.py"
