// Atlas Search (entity resolution difusa) e Vector Search (motivo semanticamente
// equivalente), lado a lado. Ambos falham com mensagem clara se o índice não
// estiver READY — é isso que o backend traduz em 503.
//
//   mongosh "$MONGODB_URI" queries/05_search_and_vector.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");
const searchIndex = process.env.ATLAS_SEARCH_INDEX_NAME || "people_entity_resolution";

const gemeo = db.people.findOne({ near_duplicate_of: { $exists: true } });
const real = db.people.findOne({ _id: gemeo.near_duplicate_of });
print(`gêmeo com erro de digitação: "${gemeo.name}"`);
print(`nome real na rede:           "${real.name}" (${real.ring_id})\n`);

const t0 = Date.now();
const achados = db.people
  .aggregate([
    {
      $search: {
        index: searchIndex,
        compound: {
          should: [
            { text: { query: gemeo.name, path: "name", score: { boost: { value: 3 } } } },
            { text: { query: gemeo.name, path: "name", fuzzy: { maxEdits: 2, prefixLength: 1 } } },
            { autocomplete: { query: gemeo.name, path: "name" } },
          ],
          minimumShouldMatch: 1,
        },
      },
    },
    { $limit: 5 },
    { $project: { name: 1, ring_id: 1, score: { $meta: "searchScore" } } },
  ])
  .toArray();

print(`Atlas Search em ${Date.now() - t0} ms:`);
achados.forEach((a) => print(`  ${a.score.toFixed(2)}  ${a.name}  ${a.ring_id || ""}`));

print(`
Vector Search precisa de um vetor de consulta (chamada à Voyage), então roda pelo
backend: POST /api/search/similar-reasons, ou data-generator/embed_reasons.py para
regravar os vetores. O índice em si é criado por schema/search_indexes.py.
`);
