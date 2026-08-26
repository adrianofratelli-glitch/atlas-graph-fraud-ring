// Prova de plano de execução do traversal.
//
// Um número de latência não convence um DBA; o plano convence. O que este script
// mostra: o `$graphLookup` percorre `connections` por IXSCAN em `from`, e a razão
// entre chaves examinadas e documentos devolvidos fica próxima de 1 — ou seja, o
// índice está fazendo o trabalho, não um COLLSCAN disfarçado.
//
//   mongosh "$MONGODB_URI" queries/08_explain_traversal.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const ring = db.rings.findOne();
const entrada = ring.leader;

print("=== índices em `connections`");
db.connections.getIndexes().forEach((i) => print(`  ${i.name}  ${JSON.stringify(i.key)}`));

print("\n=== plano da aresta que o $graphLookup segue (from: 1)");
const plano = db.connections.find({ from: entrada }).explain("executionStats");
const w = plano.queryPlanner.winningPlan;
const est = plano.executionStats;
const estagio = (w.inputStage || w).stage + (w.inputStage ? ` <- ${w.inputStage.stage}` : "");
print(`  estágio: ${JSON.stringify(w).includes("IXSCAN") ? "IXSCAN" : estagio}`);
print(`  índice usado: ${JSON.stringify((w.inputStage && w.inputStage.indexName) || w.indexName || "—")}`);
print(`  documentos devolvidos: ${est.nReturned}`);
print(`  chaves examinadas:     ${est.totalKeysExamined}`);
print(`  documentos examinados: ${est.totalDocsExamined}`);
print(`  tempo:                 ${est.executionTimeMillis} ms`);
const usouIndice = JSON.stringify(w).includes("IXSCAN");
if (!usouIndice) {
  print("  ⚠️  COLLSCAN — o índice `from_1` não existe ou não foi escolhido.");
  print("      Rode data-generator/materialize_connections.py, que recria os índices.");
  print("      Sem ele o BFS varre a coleção inteira a cada nível do traversal.");
} else {
  const razao = est.nReturned ? (est.totalKeysExamined / est.nReturned).toFixed(2) : "n/a";
  print(`  chaves por documento devolvido: ${razao}  ${razao <= 1.2 ? "(índice seletivo)" : "(revisar seletividade)"}`);
}

print("\n=== o traversal inteiro, com executionStats");
const agg = db.people
  .aggregate(
    [
      { $match: { _id: entrada } },
      {
        $graphLookup: {
          from: "connections",
          startWith: "$_id",
          connectFromField: "to",
          connectToField: "from",
          as: "network",
          maxDepth: 3,
          depthField: "hops",
        },
      },
      { $project: { n: { $size: "$network" } } },
    ],
    { explain: true }
  );

// O $graphLookup não expõe estatísticas por nível; o que dá para provar é o
// estágio de entrada e que a coleção alvo não é shardada (restrição de LIMITATIONS §1).
const entradaPlano = agg.stages ? agg.stages[0] : agg;
print(`  estágio de entrada: ${JSON.stringify(entradaPlano).includes("IDHACK") ? "IDHACK (_id)" : "ver JSON"}`);
print(`  coleção alvo shardada? ${db.connections.stats().sharded ? "SIM — ver LIMITATIONS.md §1" : "não"}`);

print("\n=== por que `to: 1` também existe");
print("  connectFromField='to' é lido a cada nível para montar o próximo lote de");
print("  buscas. Sem o índice em `to`, cada nível paga um COLLSCAN.");
const semTo = db.connections.find({ to: entrada }).explain("executionStats").executionStats;
print(`  find({to}) -> ${semTo.nReturned} docs, ${semTo.totalKeysExamined} chaves, ${semTo.executionTimeMillis} ms`);
