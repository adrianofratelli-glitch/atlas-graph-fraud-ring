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
  ["transactions", { to_pix_key: 1 }, { sparse: true }, "materialização de same_pix_counterparty"],
  // Sem ele, `POST /api/demo/reset` varre as 600 mil transações para achar as
  // poucas simuladas e estoura o tempo — o reset falhava no meio da demo.
  ["transactions", { simulated: 1 }, { sparse: true }, "reset da demo remove só as transações simuladas"],
  // `embed_reasons.py` faz um `update_many` por texto distinto, filtrando por
  // `reason_embedding: {$exists: false}`. Sem este índice são 33 COLLSCANs de
  // 600 mil documentos, e o backfill leva dezenas de minutos.
  ["transactions", { reason_text: 1, reason_embedding: 1 }, {}, "backfill de embeddings sem COLLSCAN"],

  // --- entidades ---
  ["accounts", { person_id: 1 }, {}, "conta -> pessoa"],
  ["accounts", { ring_id: 1 }, { sparse: true }, "validação de ground truth na demo"],
  ["accounts", { status: 1 }, {}, "listar contas sob investigação"],
  ["accounts", { case_id: 1 }, { sparse: true }, "contas de um caso aberto"],
  // Único por conta, e é o DICT que exige isso: uma chave endereça uma conta só
  // (Resolução BCB nº 1/2020). O índice único é a guarda contra o modelo errado
  // voltar por descuido — se alguém tentar dar a mesma chave a duas contas, a
  // escrita falha em vez de gerar uma aresta que não existe na vida real.
  ["accounts", { pix_key: 1 }, { unique: true, sparse: true }, "chave PIX única por conta (DICT)"],
  ["people", { ring_id: 1 }, { sparse: true }, "ground truth"],
  ["people", { "addresses.address_id": 1 }, {}, "materialização de shares_address"],

  ["people", { seed_index: 1 }, {}, "seleção determinística de casos na demo"],
];

for (const [coll, keys, opts, why] of specs) {
  const name = db[coll].createIndex(keys, opts);
  print(`  ${coll}.${name}  — ${why}`);
}

print("\níndices B-tree prontos.");
print("Atlas Search e Vector Search são criados por schema/search_indexes.py (não são índices de banco).");
