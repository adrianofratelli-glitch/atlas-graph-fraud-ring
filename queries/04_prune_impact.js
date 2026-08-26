// Impacto medido da poda de hub. Roda a mesma expansão com e sem
// restrictSearchWithMatch e imprime a diferença — o número que vai para
// queries/benchmarks.md.
//
//   mongosh "$MONGODB_URI" queries/04_prune_impact.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const ring = db.rings.findOne();
const entrada = db.people.findOne({ ring_id: ring.ring_id, _id: { $ne: ring.leader } })._id;

function medir(profundidade, podar) {
  const stage = {
    from: "connections",
    startWith: "$_id",
    connectFromField: "to",
    connectToField: "from",
    as: "network",
    maxDepth: profundidade,
    depthField: "hops",
  };
  if (podar) stage.restrictSearchWithMatch = { weight: { $lte: 50 } };
  const t0 = Date.now();
  const r = db.people
    .aggregate([
      { $match: { _id: entrada } },
      { $graphLookup: stage },
      { $project: { nos: { $size: { $setUnion: ["$network.to", []] } } } },
    ])
    .toArray()[0];
  return { nos: r.nos, ms: Date.now() - t0 };
}

print("profundidade | sem poda            | com poda");
for (const d of [2, 3, 4, 5]) {
  const sem = medir(d, false);
  const com = medir(d, true);
  print(`     ${d}       | ${String(sem.nos).padStart(6)} nós ${String(sem.ms).padStart(5)} ms | ${String(com.nos).padStart(6)} nós ${String(com.ms).padStart(5)} ms`);
}
