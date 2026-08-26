// Padrão A — a aresta é inferida em tempo de query a partir de `device_id`.
// Nenhuma coleção de arestas envolvida. Compare o tempo com o script 01.
//
//   mongosh "$MONGODB_URI" queries/02_graphlookup_shared_attributes.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const ring = db.rings.findOne();
const pessoa = db.people.findOne({ ring_id: ring.ring_id, _id: { $ne: ring.leader } })._id;
const conta = db.accounts.findOne({ person_id: pessoa })._id;

// Partir de uma transação *da rede*: a mesma conta também tem tráfego legítimo,
// e o dispositivo próprio dela não é compartilhado com ninguém.
const partida = db.transactions.findOne({ from_account: conta, ring_id: { $ne: null } });

print(`conta de entrada: ${conta}`);
print(`transação de partida: ${partida ? partida._id : "(nenhuma da rede)"}\n`);

for (const profundidade of [1, 2, 3]) {
  const t0 = Date.now();
  const r = db.transactions
    .aggregate([
      { $match: { from_account: conta, ring_id: { $ne: null } } },
      { $limit: 1 },
      {
        $graphLookup: {
          from: "transactions",
          startWith: "$device_id",
          connectFromField: "device_id",
          connectToField: "device_id",
          as: "network",
          maxDepth: profundidade,
          depthField: "hops",
        },
      },
      {
        $project: {
          transacoes: { $size: "$network" },
          contas: { $size: { $setUnion: ["$network.from_account", []] } },
        },
      },
    ])
    .toArray()[0];
  print(`  maxDepth ${profundidade}: ${r.contas} contas, ${r.transacoes} transações, ${Date.now() - t0} ms`);
}

print(`
Nota: o Padrão A devolve transações, não pessoas. Converter em nós de pessoa
exigiria os $lookup que o Padrão B já pagou uma vez, no job de materialização.
`);
