// Índices B-tree obrigatórios. Rodar antes de qualquer consulta de cadeia:
//   mongosh "$MONGODB_URI" schema/indexes.js
//
// createIndex é idempotente: rodar de novo não duplica nem recria.

const target = process.env.MONGODB_DB || "graph_grupo_economico";
db = db.getSiblingDB(target);
print(`banco: ${target}`);

const specs = [
  // --- consulta pontual por chave de negócio: o caminho principal da demo ---
  ["companies", { cnpj: 1 }, { unique: true }, "lookup por CNPJ"],
  ["companies", { razao_social: 1 }, {}, "apoio à busca por nome"],
  ["companies", { is_holding: 1 }, { sparse: true }, "seleção de casos na demo"],
  ["companies", { credit_status: 1 }, { sparse: true }, "empresas sob revisão de crédito"],
  ["companies", { case_id: 1 }, { sparse: true }, "empresas de um caso aberto"],
  ["companies", { seed_index: 1 }, {}, "seleção determinística de casos"],

  // --- traversal da cadeia societária ---
  // A aresta é dirigida, e as duas direções usam campos opostos: subir a cadeia
  // parte de `owned_id`, descer parte de `owner_id`. Faltando um dos dois, o
  // `$graphLookup` daquele sentido cai em collection scan por nível do BFS.
  ["ownership", { owner_id: 1 }, {}, "connectToField ao descer / connectFromField ao subir"],
  ["ownership", { owned_id: 1 }, {}, "connectToField ao subir / connectFromField ao descer"],
  // Não existe índice só em `owner_type`: três valores em 2,5 milhões de arestas
  // não seleciona nada, e toda consulta que usa o campo já entra por `owner_id`
  // ou `owned_id` e filtra depois.
  ["ownership", { simulated: 1 }, { sparse: true }, "reset da demo remove só as arestas simuladas"],

  // --- exposição de crédito ---
  ["credit_exposure", { company_id: 1 }, { unique: true }, "exposição por empresa"],
  ["credit_exposure", { advisor_id: 1 }, {}, "soma da carteira sem junção por documento"],
  ["credit_exposure", { case_id: 1 }, { sparse: true }, "exposições de um caso aberto"],
  ["credit_exposure", { vencido: -1 }, {}, "encontrar inadimplência relevante"],

  // --- hierarquia comercial: árvore auto-referente ---
  // `reports_to` é o connectToField do traversal que desce do gerente para os
  // assessores. Sem ele o $graphLookup varre `advisors` por nível.
  ["advisors", { reports_to: 1 }, {}, "connectToField ao descer a hierarquia"],
  ["advisors", { papel: 1 }, {}, "seleção de gerente/assessor na demo"],
  ["advisors", { matricula: 1 }, { unique: true }, "login por matrícula"],
  ["companies", { advisor_id: 1 }, {}, "carteira de um assessor"],

  // --- sócios pessoa física ---
  // Só `seed_index`. Ocupação, renda e faixa etária são **projeção** no painel do
  // nó, nunca filtro nem ordenação: índice sobre elas custa escrita e não paga
  // leitura nenhuma. Vinham do modelo anterior desta POV, em que o atributo
  // compartilhado era a própria aresta.
  ["people", { seed_index: 1 }, {}, "seleção determinística"],
];

for (const [coll, keys, opts, why] of specs) {
  const name = db[coll].createIndex(keys, opts);
  print(`  ${coll}.${name}  — ${why}`);
}

print("\níndices B-tree prontos.");
print("Atlas Search é criado por schema/search_indexes.py (não é índice de banco).");
