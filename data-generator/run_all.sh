#!/bin/bash
# Pipeline completo de dados, na ordem correta. Idempotente de ponta a ponta.
#
# A ordem importa em um ponto: `generate_ownership.py` lê `people` para sortear os
# sócios pessoa física. Rodar fora de ordem gera uma base societária sem sócios.
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BASE/.venv/bin/python"
PEOPLE="${PEOPLE:-800000}"
COMPANIES="${COMPANIES:-1200000}"
GROUPS="${GROUPS:-40000}"
SHOWCASE="${SHOWCASE:-40}"

# Diz em voz alta o volume que vai gerar, e de onde veio cada número.
#
# Uma execução herdou `GROUPS=20` do ambiente e produziu 1.049 participações
# entre PJ em vez de 249 mil — uma base que carrega igual, roda igual e mostra
# um grafo vazio na hora da demo. Volume herdado em silêncio é o pior tipo de
# defeito: não falha, só entrega menos.
for var in PEOPLE COMPANIES GROUPS SHOWCASE; do
  if [[ -n "${!var+x}" && -n "$(printenv "$var" || true)" ]]; then
    echo "   $var=$(printenv "$var")  (do ambiente)"
  else
    echo "   $var=${!var}  (padrão)"
  fi
done

echo "▶ 1/5 sócios pessoa física ($PEOPLE)"
"$PY" "$BASE/data-generator/generate_people.py" --people "$PEOPLE" "$@"

echo "▶ 2/5 base societária ($COMPANIES empresas, $GROUPS grupos)"
"$PY" "$BASE/data-generator/generate_ownership.py" --companies "$COMPANIES" --groups "$GROUPS" --showcase "$SHOWCASE"

echo "▶ 3/5 hierarquia comercial (gerentes e assessores)"
"$PY" "$BASE/data-generator/generate_advisors.py"

echo "▶ 4/5 índices B-tree"
mongosh "$(grep '^MONGODB_URI=' "$BASE/.env" | cut -d= -f2-)" --quiet "$BASE/schema/indexes.js"

echo "▶ 5/5 conferindo os índices do traversal"
mongosh "$(grep '^MONGODB_URI=' "$BASE/.env" | cut -d= -f2-)" --quiet --eval '
db = db.getSiblingDB(process.env.MONGODB_DB || "graph_grupo_economico");
const nomes = db.ownership.getIndexes().map(i => i.name);
for (const req of ["owner_id_1", "owned_id_1"]) {
  if (!nomes.includes(req)) { print("FALTA o índice " + req + " em ownership"); quit(1); }
}
print("  ok: " + nomes.join(", "));'

echo "✅ dados prontos. Próximo: .venv/bin/python schema/search_indexes.py"
