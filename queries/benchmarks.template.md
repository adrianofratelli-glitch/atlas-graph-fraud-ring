# Benchmarks

> Números medidos por `queries/bench.py` contra o cluster real. Nunca
> substituídos por estimativa ou por número de mercado — o valor deste documento
> é ser reproduzível pelo cliente com um comando.
>
> ```bash
> .venv/bin/python queries/bench.py --runs 20
> ```
>
> A primeira execução de cada cenário é descartada: ela paga o aquecimento de
> cache do WiredTiger e mediria a coisa errada.

## Ambiente de teste

*(preenchido pela última execução — ver `queries/bench-results.json` para o JSON bruto)*

| | |
|---|---|
| Tier do cluster | M20 (4 GB RAM, 150 GB disco), compartilhado com outras aplicações |
| Latência de rede até o cluster | **PING_MEAN ms** de média (p95 PING_P95 ms) |
| Volume | COUNTS |
| Redes injetadas | 40 (20 a 30 membros cada) |
| Data | MEASURED_AT |

**Leia a latência de rede antes de qualquer outro número.** O cliente da demo roda
numa máquina local contra um cluster remoto, então todo tempo abaixo já inclui um
piso de ~PING_MEAN ms de rede. O que o benchmark mede é o **incremento** sobre esse
piso — que é a parte atribuível ao `$graphLookup`.

## Aviso sobre a primeira versão destes números

Os benchmarks publicados antes de 2026-08-26 foram medidos com `connections`
**sem os índices de traversal**. `materialize_connections.py --rebuild` faz
`drop()`, que leva os índices junto, e o pipeline os criava num passo anterior —
então o BFS rodava por COLLSCAN de 117.974 documentos em cada nível. Nada
quebrava; só ficava lento.

Efeito da correção na profundidade 3: **525 ms → 275 ms**. Os índices agora são
criados pela própria materialização, `run_all.sh` confere que existem, e
`tests/test_resilience.py` falha se o plano voltar a ser COLLSCAN.

Está registrado aqui porque um número de benchmark que mudou pela metade merece
explicação, não substituição silenciosa.

## `$graphLookup` — Padrão B (arestas explícitas em `connections`)

| Profundidade | Média (ms) | p95 (ms) | Nós retornados |
|---|---|---|---|
TABLE_B

## `$graphLookup` — Padrão A (atributo implícito via `device_id`)

| Profundidade | Média (ms) | p95 (ms) | Contas alcançadas |
|---|---|---|---|
TABLE_A

O Padrão A devolve **transações**, não pessoas. Converter em nós de pessoa
exigiria os `$lookup` que o Padrão B já pagou uma vez, no job de materialização.
Comparar os dois tempos diretamente é injusto com o Padrão B: ele entrega mais.

## Impacto de `restrictSearchWithMatch` (poda de hubs)

| Cenário (profundidade 4) | Média (ms) | Nós retornados |
|---|---|---|
TABLE_PRUNE

## Transação ACID — marcar a rede como sob investigação

Medido pelo endpoint `POST /api/investigation/flag`, que é o mesmo caminho da demo:

| Tamanho da rede | Contas atualizadas | Tempo (ms) |
|---|---|---|
| 28 pessoas | 44 | ~1.050 |

`readConcern: snapshot`, `writeConcern: majority`, três escritas no mesmo commit
(`accounts`, `people`, `investigations`).

## Change Streams — latência do alerta

Medida pelo próprio listener (`lookup_ms` no payload do alerta) e visível na UI a
cada disparo. A verificação de rede — o `find` em `accounts` que decide se a
transação toca um caso aberto — roda em uma casa de dezenas de milissegundos; o
tempo dominante entre a inserção e o alerta na tela é a propagação do oplog somada
ao RTT de rede.

## Escala — traversal sobre 2,4 milhões de arestas

> Reproduzir: `.venv/bin/python tests/scale_graph.py --build --measure`
>
> As redes de fraude injetadas têm 30 membros e formam componentes fechados. Elas
> provam que o traversal **encontra** a rede; não provam nada sobre custo, porque
> o BFS nunca faz trabalho pesado. Esta seção existe para responder "e com o meu
> volume?" com número, não com adjetivo.

**Grafo de teste:** anexação preferencial sobre as mesmas 150 mil pessoas,
**2.398.814 arestas dirigidas**, cauda longa de grau com teto de 400 (mesmo
espírito do `HUB_FANOUT_THRESHOLD`). Coleção `connections_scale`, indexada em
`from` e `to`.

### Custo por grau do nó de entrada

| Entrada | Profundidade | Nós alcançados | Arestas | Tempo |
|---|---|---|---|---|
| grau 398 | 1 | 16.593 | 19.642 | **483 ms** |
| grau 398 | 2 | — | — | ✗ **estoura 100 MB, após 97 s** |
| grau 11 | 1 | 167 | 178 | 259 ms |
| grau 11 | 2 | 6.229 | 6.694 | 363 ms |
| grau 11 | 3 | **100.705** | 211.471 | **23,6 s** |
| grau 18 | 1 | 353 | 372 | 262 ms |
| grau 18 | 2 | 11.337 | 12.678 | 345 ms |
| grau 18 | 3 | — | — | ✗ **estoura 100 MB, após 41 s** |

**O custo é o tamanho do componente alcançado, não o número em `maxDepth`.** Um nó
de grau 11 na profundidade 2 custa 363 ms; o mesmo nó na profundidade 3 alcança
dois terços do grafo e custa 23,6 s. A mesma profundidade 3, a partir de um nó de
grau 18, nem termina.

### A poda é o que torna a query possível

Este é o número que muda a conversa. Mesma entrada (grau 18), mesma profundidade
3, com e sem `restrictSearchWithMatch`:

| | Resultado |
|---|---|
| sem poda | ✗ estoura o limite de 100 MB após **40,7 s** |
| **com poda** (`weight <= 5`) | **33.872 nós · 45.318 arestas · 1.044 ms** |

De impossível para um segundo. E na profundidade 1 a partir do nó de grau 398:
16.593 nós em 400 ms sem poda, 5.344 nós em 313 ms com.

`restrictSearchWithMatch` poda **durante** o traversal, antes de o array de saída
crescer. Um `$match` depois do `$graphLookup` não teria salvado nada — o estágio
já teria estourado.

### O que estes números dizem, e o que não dizem

**Dizem:** num grafo de 2,4 M de arestas num M20 compartilhado, traversal de 1 a 2
saltos é interativo (260–500 ms) para qualquer perfil de grau, e a profundidade 3
exige poda para existir. Isso é utilizável para investigação de fraude, que é um
caso de 1 a 3 saltos.

**Não dizem** nada sobre bilhões de arestas, sobre grafo shardado (`LIMITATIONS.md
§1`), nem sobre algoritmos contínuos (`§3`). E o teto de 100 MB
(`§5`) é estrutural: não some com tier maior.

**O que mudaria:** tier com mais RAM reduz o tempo dos casos que cabem, mas não
levanta o teto de 100 MB. Para componentes maiores que isso, a resposta é
pré-computar subgrafos, não comprar máquina.

## Conclusões honestas

**O traversal do anel virou ruído de rede.** Com os índices no lugar, o trabalho
atribuível ao `$graphLookup` é a diferença entre a coluna de média e o piso de
rede de ~256 ms: cerca de **21 ms na profundidade 1** e **6 ms na profundidade 5**
— ou seja, dentro da variação da própria medição. A curva é plana porque a rede
satura em 30 nós na profundidade 3 e não há mais nada a alcançar.

Isso é bom e é insuficiente ao mesmo tempo. Prova que o traversal encontra a rede
sem custo perceptível; **não prova nada sobre volume**, porque o BFS nunca faz
trabalho pesado. Para responder "e com o meu volume?", use a seção *Escala*.

**A poda em tempo de query continua sem ajudar aqui.** Profundidade 4: 273 ms sem
poda, 284 ms com, os mesmos 30 nós. Ela não tem nada a remover porque a
materialização já removeu os hubs, e o filtro extra por aresta é custo puro —
agora dentro do ruído, mas ainda do lado errado do zero.

O corolário é a lição de arquitetura: **a poda que importa é a da materialização,
não a da query**. `materialize_connections.py --report-only` mostra o que ela
descarta neste dataset:

| Tipo de hub | Grupos descartados | Alcance |
|---|---|---|
| endereço | 3 | 8.618 pessoas |
| dispositivo | 5 | 35.251 contas |

Sem esse descarte, essas 8.618 pessoas ficariam mutuamente conectadas em um salto,
e qualquer traversal de profundidade 2 varreria dezenas de milhares de nós. A poda
em tempo de query continua exposta na UI porque em uma base real, onde as arestas
chegam prontas de um sistema anterior, ela volta a ser o controle principal.

**Padrão A e Padrão B não são comparáveis pelo relógio.** Os dois ficam em ~265 ms
porque os dois estão no piso de rede; o Padrão A alcança 2 contas e o Padrão B
alcança 30 nós de pessoa. A razão para preferir o Padrão B na investigação também
não é velocidade: é `restrictSearchWithMatch`, que só existe com a aresta
materializada — podar por tipo e por peso é impossível quando a aresta é inferida
em tempo de query.

**O que mudaria estes números:** um cluster na mesma região (elimina os 256 ms de
piso), um tier maior (o M20 tem 4 GB e é compartilhado), e um grafo grande o
bastante para o BFS custar mais que a rede — que é exatamente o que a seção
seguinte mede.
