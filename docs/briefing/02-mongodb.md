# 02 — MongoDB

Modelagem detalhada, com o raciocínio de escolha entre os dois padrões de grafo,
está em [`schema/collections.md`](../../schema/collections.md). Este arquivo cobre
o que roda contra o cluster.

## Coleções

| Coleção | Papel | Origem |
|---|---|---|
| `people` | entidades do grafo | `generate_synthetic_data.py` |
| `accounts` | contas, 1-3 por pessoa | idem |
| `devices` | um por conta + 5 hubs deliberados | idem |
| `transactions` | 600k, com `reason_text` e `reason_embedding` | idem + `embed_reasons.py` |
| `connections` | arestas materializadas, bidirecionais | `materialize_connections.py` |
| `rings` | ground truth das redes injetadas | `inject_fraud_rings.py` |
| `investigations` | casos abertos pela transação ACID | backend |
| `alerts` | disparos do change stream | backend |

## Índices

> **Armadilha que custou caro nesta POV.** `materialize_connections.py --rebuild`
> faz `drop()` em `connections`, e `drop()` leva os índices junto. Como o
> pipeline criava os índices num passo anterior, o traversal rodou por
> **COLLSCAN de 117.974 documentos em cada nível do BFS** — e nada quebrou, só
> ficou lento. Os primeiros benchmarks publicados foram medidos assim.
>
> Correção: os índices de `connections` são criados **pela própria
> materialização**, no fim dela, e `run_all.sh` confere que existem antes de
> declarar sucesso. `queries/08_explain_traversal.js` e o bloco *Plano de
> execução* de `tests/test_resilience.py` são a guarda de regressão.
>
> Efeito medido da correção, profundidade 3: **525 ms → 275 ms**, contra um piso
> de rede de 256 ms. O traversal do anel passou a custar ~20 ms de trabalho real.

`schema/indexes.js` cria os B-tree das demais coleções; `schema/search_indexes.py` cria os de busca,
que têm ciclo de vida próprio (`PENDING` → `BUILDING` → `READY`) e por isso não
cabem no mesmo script.

Os dois que sustentam o traversal são `connections.{from:1}` e
`connections.{to:1}` — `connectFromField` e `connectToField` do `$graphLookup`.
Sem eles a expansão vira collection scan por nível de profundidade.

`connections.{type:1, from:1}` existe para o `restrictSearchWithMatch` por tipo de
aresta, que é como o frontend liga e desliga dispositivo/endereço/PIX.

## Materialização de arestas: a regra que importa

Um atributo só vira aresta se seu fan-out estiver **abaixo de
`HUB_FANOUT_THRESHOLD`** (padrão 50). Um dispositivo usado por 800 contas, ou o
endereço de uma agência bancária, não é evidência de vínculo — materializá-lo
transformaria metade da base em um único componente conexo.

`shares_device` é construída só a partir de `from_account`, deliberadamente.
Incluir `to_account` transformaria "A pagou B do celular de A" em aresta de
dispositivo compartilhado, que não é vínculo nenhum. Foi medido: com `to_account`
incluso, o grau médio da população limpa sobe de ~1 para 8.6 e a profundidade 2
varre metade da base.

## Pipelines

| Arquivo | O que demonstra |
|---|---|
| `queries/01_graphlookup_explicit_edges.js` | Padrão B, tempo por profundidade |
| `queries/02_graphlookup_shared_attributes.js` | Padrão A, para comparação |
| `queries/03_hops_between.js` | distância em saltos via `depthField` |
| `queries/04_prune_impact.js` | impacto medido de `restrictSearchWithMatch` |
| `queries/05_search_and_vector.js` | entity resolution difusa |
| `queries/06_transaction_flag_ring.js` | transação ACID multi-documento |
| `queries/bench.py` | preenche `queries/benchmarks.md` com números medidos |

## Transação ACID

Escopo: `accounts.status`, `people.risk_flags` e um documento em `investigations`.
`readConcern: snapshot`, `writeConcern: majority`. Ou a rede toda entra em
investigação, ou nenhum nó entra — um estado intermediário (metade das contas
bloqueada, sem registro de auditoria coerente) é pior do que não ter agido.

O endpoint recusa acima de 5000 nós: uma rede desse tamanho é trabalho de job em
lote, não de uma transação única.

## Manutenção incremental de arestas

O job em lote leva ~15 minutos. Em produção, o grafo não pode esperar por ele: o
mesmo change stream que dispara o alerta também materializa a aresta
(`app/services/edge_maintenance.py`). Uma transação nova entra, o vínculo por
dispositivo aparece em `connections` em **~2 s**, com `source: "change_stream"`,
e o `$graphLookup` seguinte já o percorre.

As regras do batch são preservadas literalmente — `_id` determinístico no mesmo
namespace, só `from_account`, hub acima do limiar não vira aresta. Se divergirem,
o grafo incremental e o reconstruído deixam de ser o mesmo grafo.

O que o incremental **não** faz: podar aresta que deixou de fazer sentido, e
backfill de janela anterior ao listener. O padrão correto é os dois — change
stream para frescor, batch periódico para consistência e poda. Ver
`docs/adr/0003-manutencao-incremental-de-arestas.md`.

## Change Streams

`transactions.watch()` em thread própria, com `full_document="updateLookup"`,
`max_await_time_ms` (para não bloquear o shutdown) e **`resume_token` guardado**,
para retomar de onde parou se o cursor cair em failover. Cada assinante SSE tem
fila limitada em 200 eventos: um cliente lento não pode fazer o listener crescer
memória sem teto.

## Seeds e reprodutibilidade

Semente fixa (`20260826`) no gerador e no `Faker`. `_id` determinístico em tudo.
Rodar o pipeline de novo produz exatamente o mesmo dataset — inclusive o mesmo
`ring_000` para o roteiro da demo.
