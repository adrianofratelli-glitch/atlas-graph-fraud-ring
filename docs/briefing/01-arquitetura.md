# 01 — Arquitetura

## Camadas

```
┌──────────────────────┐   ┌──────────────────────┐   ┌─────────────────────────┐
│ React + Vite (5350)   │──▶│ FastAPI (8350)        │──▶│ MongoDB Atlas            │
│ vis-network            │◀──│ app/db  · acesso      │◀──│ $graphLookup             │
│ EventSource (SSE)      │   │ app/services · orq.   │   │ Atlas Search             │
│ tokens do design system│   │ AlertHub (thread)     │   │ Atlas Vector Search      │
└──────────────────────┘   └──────────────────────┘   │ Change Streams           │
                                                         │ transação ACID           │
                                                         └─────────────────────────┘
                                                                     ▲
                                                          ┌──────────┴──────────┐
                                                          │ data-generator/      │
                                                          │ idempotente          │
                                                          └─────────────────────┘
```

Regra de camada: **nenhuma rota importa `pymongo`**. `main.py` só expõe HTTP e
traduz exceção em código de status; toda query vive em `backend/app/db/`. Isso é
o que permite trocar driver ou versão sem tocar nas rotas.

## Invariantes

1. **`_id` determinístico em todo dado gerado.** `det_id(kind, *parts)` é
   `uuid5` sobre os atributos-chave. Rodar o gerador duas vezes reescreve os
   mesmos documentos — nenhuma escrita usa `ObjectId` aleatório.
2. **O backend decide a profundidade, não o frontend.** `clamp_depth()` limita ao
   `GRAPH_MAX_DEPTH_CAP` (padrão 6). Uma expansão sem teto é o jeito mais rápido
   de travar uma demo ao vivo.
3. **Retry só para falha transitória de rede.** `with_retry()` repete
   `AutoReconnect`, `NetworkTimeout` e `ConnectionFailure` com backoff
   exponencial, no máximo 3 vezes. Erro de lógica ou de validação nunca é
   repetido — repetir esconde bug.
4. **Degradação por recurso, não por tela.** Índice de Search/Vector ausente ou
   `BUILDING` vira `503` com `{feature, index, status}`; o frontend mostra um
   badge naquele painel e o traversal continua funcionando.
5. **Ground truth rastreável.** Toda rede injetada tem `ring_id` em `people`,
   `accounts` e `transactions`, e um resumo em `rings`. A demo nunca depende de a
   aleatoriedade ter cooperado.
6. **Teto de nós devolvidos.** `GRAPH_MAX_NODES` (padrão 1200) trunca pela menor
   distância em saltos. O gargalo em profundidade alta é o navegador, não o Atlas;
   truncar pela periferia preserva o que interessa e o payload informa
   `truncated: true` para a UI dizer isso em voz alta.
7. **Teto de tempo, não só de tamanho.** Todo traversal roda com `maxTimeMS`
   (`GRAPH_MAX_TIME_MS`, padrão 15 s). Medido num grafo de 2,4 M de arestas, o
   `$graphLookup` moeu **97 segundos** antes de estourar o limite de 100 MB do
   documento de saída. Sem o teto, isso é uma tela parada por um minuto e meio
   para no fim dar erro. Com ele, a rota devolve `503` com `too_large: true`, o
   motivo e o que fazer a respeito.
8. **Corpo de POST passa por schema, não por checagem manual.** Os modelos
   `pydantic` no topo de `main.py` existem porque dois bugs reais vieram de
   validação artesanal: `limit: -1` chegava ao `$vectorSearch` e virava 500, e
   `person_ids` como string era iterado caractere a caractere até a transação
   abortar com 409.

## Variáveis de ambiente

| Variável | Padrão | Papel |
|---|---|---|
| `MONGODB_URI` | — | obrigatória |
| `MONGODB_DB` | `graph_fraud_ring` | banco da POV |
| `ATLAS_SEARCH_INDEX_NAME` | `people_entity_resolution` | entity resolution |
| `VECTOR_INDEX_NAME` | `transactions_reason_vector` | similaridade semântica |
| `VOYAGE_API_KEY` | — | sem ela, Vector Search degrada com `NO_EMBEDDING_KEY` |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `voyage-3-lite` / `512` | ver ADR 0002 |
| `GRAPH_MAX_DEPTH_CAP` | `6` | teto absoluto de profundidade |
| `GRAPH_DEFAULT_DEPTH` | `4` | usado quando o cliente não manda profundidade |
| `HUB_FANOUT_THRESHOLD` | `50` | limiar de poda de hub |
| `GRAPH_MAX_NODES` | `1200` | teto de nós no payload |
| `GRAPH_MAX_TIME_MS` | `15000` | teto de tempo da agregação de traversal |

## Como rodar

```bash
cp .env.example .env      # preencher MONGODB_URI e VOYAGE_API_KEY

# dependências
python3 -m venv .venv && .venv/bin/pip install -r data-generator/requirements.txt
python3 -m venv backend/venv && backend/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)

# dados (idempotente; ~20 min para o volume padrão)
bash data-generator/run_all.sh
.venv/bin/python schema/search_indexes.py       # espera ficar READY
.venv/bin/python data-generator/embed_reasons.py

# subir
./start.sh                 # DEV=1 ./start.sh para HMR e --reload
```

## Ordem do trabalho

Dados, índices, queries em `mongosh`, backend, frontend, benchmarks. A topologia
do dado sintético foi decidida por medição antes de existir uma linha de backend —
sem isso, a demo mostra ou um clique inteiro na profundidade 1, ou metade da base
na profundidade 2. Ver `docs/adr/0001-topologia-do-dado-sintetico.md`.

## Endpoints

| Método | Rota | Papel |
|---|---|---|
| GET | `/health/live` | liveness puro, não toca no banco |
| GET | `/health` | checagem profunda: conexão, contagens, índices de busca, latência de referência do `$graphLookup`, estado do change stream |
| GET | `/api/entry-points` | pontos de entrada da demo, do ground truth em `rings` |
| GET | `/api/network/{person_id}` | Padrão B — expansão por arestas explícitas (`depth`, `edge_types`, `prune_hubs`) |
| GET | `/api/network-by-device/{account_id}` | Padrão A — traversal por `device_id`, para comparação |
| GET | `/api/hops` | distância em saltos entre duas pessoas |
| GET | `/api/search/people` | Atlas Search com `fuzzy` + `autocomplete` |
| POST | `/api/search/similar-reasons` | Vector Search sobre `reason_embedding`, agrupado por texto |
| POST | `/api/investigation/flag` | transação ACID multi-documento |
| POST | `/api/investigation/close/{case_id}` | fecha um caso |
| POST | `/api/demo/reset` | volta o dataset ao estado pré-demo |
| POST | `/api/demo/simulate-transaction` | injeta transação que toca a rede sinalizada |
| POST | `/api/demo/link-accounts` | insere transações que fazem duas pessoas sem vínculo dividirem um dispositivo; quem cria a aresta é o change stream |
| GET | `/api/connections/between` | existe aresta entre duas pessoas? usado para provar o antes e o depois |
| GET | `/api/alerts/stream` | SSE alimentado pelo change stream |
| GET | `/api/alerts/recent` | últimos alertas persistidos |

## Armadilhas encontradas na construção

Registradas porque custaram tempo e voltariam em qualquer POV parecida:

- **`shares_device` a partir das duas pontas da transação.** "A pagou B do celular
  de A" não é dispositivo compartilhado. Incluir `to_account` na materialização
  levou o grau médio da população limpa de ~1 para 8.6, e a profundidade 2 passou
  a varrer metade da base.
- **Gêmeo com erro de digitação colidindo com o anel seguinte.** Um off-by-one no
  recrutamento fazia o gêmeo do anel *r* ser o líder do anel *r+1*, e o nome com
  typo sobrescrevia o do líder — quebrando a correspondência que o passo 6 do
  roteiro depende. O sintoma era sutil: a demo funcionava, só apontava para a
  pessoa errada.
- **Troca de caractere identidade no gerador de typo.** `"e" -> "e"` produzia um
  gêmeo com nome exatamente igual, que o Atlas Search resolve por casamento
  exato — o `fuzzy` nunca era exercitado.
- **`connections` defasada em relação ao conjunto de membros.** Re-injetar as
  redes sem rematerializar as arestas deixou ~3% dos membros sem aresta. Um deles
  sorteado como ponto de entrada abre o grafo vazio na frente do cliente; por isso
  `_entry_node` só escolhe nó com arestas confirmadas.
- **`ObjectId` no payload do alerta.** O SSE serializa em JSON; `alerts._id` virou
  string derivada do `_id` da transação, e a escrita virou upsert para que um
  `update` na mesma transação não mate a thread do listener com chave duplicada.
- **`depthField` devolve `NumberLong`.** Em `mongosh`, `long + 1` concatena string
  em vez de somar. Os scripts usam `$toInt`.

## Testes

```bash
.venv/bin/python tests/test_resilience.py           # suíte hostil completa
.venv/bin/python tests/test_resilience.py --quick   # sem change stream e carga
.venv/bin/python tests/scale_graph.py --build       # grafo de escala (demorado)
.venv/bin/python tests/scale_graph.py --measure     # mede o traversal em escala
.venv/bin/python tests/index_tuning.py              # ganho do índice composto
mongosh "$MONGODB_URI" queries/08_explain_traversal.js   # prova de IXSCAN
```

`tests/test_resilience.py` não é teste de unidade. Cada caso corresponde a uma
forma real de a POV falhar na frente de um cliente, e o critério de aprovação é
**degradar com mensagem clara**: nunca 500, nunca travar, nunca devolver dado
inconsistente. Os blocos:

| Bloco | O que tenta quebrar |
|---|---|
| Entradas hostis | id gigante, id inexistente, profundidade 999 e negativa, tipo de aresta inventado, operador Mongo no `_id`, busca vazia e de 5000 caracteres, metacaracteres de regex, `limit` fora de faixa, `person_ids` com tipo errado |
| Consistência do payload | aresta apontando para nó ausente, contagem divergente, mais de uma raiz, monotonicidade da profundidade, poda que aumenta alcance, não-determinismo |
| Transação ACID | marcação repetida, id inexistente, acima do teto, **8 transações concorrentes sobre os mesmos documentos** |
| Plano de execução | ausência dos índices de traversal e COLLSCAN disfarçado |
| Change Streams | rajada de 12 transações simultâneas, perda de evento, morte do listener |
| Manutenção de aresta | vínculo materializado em tempo real, duplicação por reprocessamento |
| Manutenção de aresta | vínculo criado ao vivo pelo change stream, duplicação por reprocessamento |
| Carga | 40 expansões concorrentes com 10 trabalhadores, p50/p95 |

Três bugs reais saíram daí, todos corrigidos:

- `limit: -1` chegava ao `$vectorSearch` e virava 500;
- `person_ids` como string era iterado caractere a caractere até a transação
  abortar com 409;
- o par de pessoas de `link-accounts` era fixo, então a **segunda apresentação
  seguida** falhava: as duas já estavam ligadas pela primeira. Agora é sorteado, e
  `POST /api/demo/reset` remove as arestas criadas ao vivo.

Os dois primeiros viraram validação de schema (`pydantic`) em vez de checagem
manual.

`tests/scale_graph.py` e `tests/index_tuning.py` não testam: medem. O primeiro
constrói um grafo de 2,4 milhões de arestas para descobrir onde o traversal deixa
de caber; o segundo compara `{from: 1}` com `{from: 1, weight: 1}` no caminho com
poda. Os dois escrevem JSON ao lado, e os números vão para
`queries/benchmarks.md`.
