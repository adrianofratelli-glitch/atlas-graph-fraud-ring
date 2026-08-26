// Distância em saltos entre duas pessoas, via depthField.
// NÃO é shortest-path genérico otimizado — ver LIMITATIONS.md §2.
//
//   mongosh "$MONGODB_URI" queries/03_hops_between.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const ring = db.rings.findOne();
const membros = db.people
  .find({ ring_id: ring.ring_id }, { _id: 1 })
  .toArray()
  .map((d) => d._id)
  .filter((id) => id !== ring.leader); // o líder é a origem; sem o filtro, alvo === origem
const origem = ring.leader;
const alvo = membros[membros.length - 1];

const t0 = Date.now();
const r = db.people
  .aggregate([
    { $match: { _id: origem } },
    {
      $graphLookup: {
        from: "connections",
        startWith: "$_id",
        connectFromField: "to",
        connectToField: "from",
        as: "network",
        maxDepth: 6,
        depthField: "hops",
      },
    },
    { $project: { hit: { $filter: { input: "$network", as: "e", cond: { $eq: ["$$e.to", alvo] } } } } },
    // $toInt: depthField devolve NumberLong, e `long + 1` em JS concatena string.
    { $project: { saltos: { $toInt: { $min: "$hit.hops" } }, via: { $slice: ["$hit.type", 3] } } },
  ])
  .toArray()[0];

print(`${origem}`);
print(`  -> ${alvo}`);
print(`  saltos: ${r.saltos === null ? "não conectado até profundidade 6" : r.saltos + 1}`);
print(`  tempo: ${Date.now() - t0} ms`);
