// Padrão B — traversal sobre arestas materializadas em `connections`.
// É a query que o endpoint /api/network executa.
//
//   mongosh "$MONGODB_URI" queries/01_graphlookup_explicit_edges.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

// Mesmo ponto de entrada da UI (backend/app/services/demo.py::_entry_node): o
// líder da rede, que é a raiz da árvore e torna a revelação por profundidade
// legível. Um membro sorteado pode ser uma folha, e a curva fica achatada.
const ring = db.rings.findOne();
const entrada = ring.leader;

print(`entrada: ${entrada} (líder da rede ${ring.ring_id}, ${ring.size} membros)\n`);

for (const profundidade of [1, 2, 3, 4]) {
  const t0 = Date.now();
  const r = db.people
    .aggregate([
      { $match: { _id: entrada } },
      {
        $graphLookup: {
          from: "connections",
          startWith: "$_id",
          connectFromField: "to",
          connectToField: "from",
          as: "network",
          maxDepth: profundidade,
          depthField: "hops",
          // Poda de hub: um atributo com fan-out alto não é evidência de vínculo.
          restrictSearchWithMatch: { weight: { $lte: 50 } },
        },
      },
      {
        $project: {
          arestas: { $size: "$network" },
          nos: { $size: { $setUnion: ["$network.to", []] } },
        },
      },
    ])
    .toArray()[0];
  print(`  maxDepth ${profundidade}: ${r.nos} nós, ${r.arestas} arestas, ${Date.now() - t0} ms`);
}
