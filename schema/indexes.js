// Índices obrigatórios. Rodar antes de qualquer query de grafo:
//   mongosh "$MONGODB_URI" --eval 'db = db.getSiblingDB("graph_fraud_ring")' schema/indexes.js
//
// createIndex é idempotente: rodar de novo não duplica nem recria.

const target = process.env.MONGODB_DB || "graph_fraud_ring";
db = db.getSiblingDB(target);
print(`banco: ${target}`);

const specs = [
  // --- traversal (Padrão B, edges explícitas) ---
  ["connections", { from: 1 }, {}, "connectFromField do $graphLookup"],
  ["connections", { to: 1 }, {}, "connectToField do $graphLookup"],
  ["connections", { type: 1, from: 1 }, {}, "suporte a restrictSearchWithMatch por tipo de aresta"],

  // --- traversal (Padrão A, atributo implícito) ---
  ["transactions", { device_id: 1 }, {}, "traversal por dispositivo compartilhado"],
  ["transactions", { from_account: 1 }, {}, "entrada do grafo + consulta operacional"],
  ["transactions", { to_account: 1 }, {}, "entrada do grafo + consulta operacional"],
  ["transactions", { timestamp: -1 }, {}, "extrato recente da conta"],
  ["transactions", { ring_id: 1 }, { sparse: true }, "ground truth"],

  // --- entidades ---
  ["accounts", { person_id: 1 }, {}, "conta -> pessoa"],
  ["accounts", { ring_id: 1 }, { sparse: true }, "validação de ground truth na demo"],
  ["accounts", { status: 1 }, {}, "listar contas sob investigação"],
  ["people", { ring_id: 1 }, { sparse: true }, "ground truth"],
  ["people", { "addresses.address_id": 1 }, {}, "materialização de shares_address"],
  ["people", { pix_key: 1 }, { sparse: true }, "materialização de shares_pix_key"],
  ["people", { seed_index: 1 }, {}, "seleção determinística de casos na demo"],
];

for (const [coll, keys, opts, why] of specs) {
  const name = db[coll].createIndex(keys, opts);
  print(`  ${coll}.${name}  — ${why}`);
}

print("\níndices B-tree prontos.");
print("Atlas Search e Vector Search são criados por schema/search_indexes.py (não são índices de banco).");
