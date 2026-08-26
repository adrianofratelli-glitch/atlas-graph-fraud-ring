// Fan-out de hub, medido. É a evidência por trás de LIMITATIONS.md §4.
//
// A rede de fraude é um componente fechado: mesmo na profundidade 6 o traversal
// satura nos 30 membros. A degradação por fan-out não aparece ali — ela aparece
// num hub, e é por isso que a materialização descarta hubs antes que virem aresta.
//
//   mongosh "$MONGODB_URI" queries/07_hub_fanout.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const hub = db.transactions
  .aggregate([
    { $group: { _id: "$device_id", contas: { $addToSet: "$from_account" } } },
    { $set: { n: { $size: "$contas" } } },
    { $sort: { n: -1 } },
    { $limit: 1 },
    { $project: { n: 1 } },
  ])
  .toArray()[0];

print(`dispositivo hub: ${hub._id}`);
print(`  operado por ${hub.n.toLocaleString("pt-BR")} contas distintas\n`);

const partida = db.transactions.findOne({ device_id: hub._id }, { _id: 1 });

for (const profundidade of [1, 2]) {
  const t0 = Date.now();
  const r = db.transactions
    .aggregate([
      { $match: { _id: partida._id } },
      {
        $graphLookup: {
          from: "transactions",
          startWith: "$device_id",
          connectFromField: "device_id",
          connectToField: "device_id",
          as: "net",
          maxDepth: profundidade,
          depthField: "hops",
        },
      },
      { $project: { contas: { $size: { $setUnion: ["$net.from_account", []] } }, txns: { $size: "$net" } } },
    ])
    .toArray()[0];
  print(`  maxDepth ${profundidade}: ${r.contas.toLocaleString("pt-BR")} contas, ${r.txns.toLocaleString("pt-BR")} transações, ${Date.now() - t0} ms`);
}

print(`
Um único salto a partir do hub alcança milhares de contas que não têm relação
nenhuma entre si. É por isso que materialize_connections.py nunca transforma esse
dispositivo em aresta: rode --report-only para ver quanto ele descarta.
`);
