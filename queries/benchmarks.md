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
| Latência de rede até o cluster | **8.5 ms** de média (p95 10.4 ms) |
| Volume | 150.000 people · 239.912 accounts · 240.939 devices · 603.066 transactions · 117.974 connections |
| Redes injetadas | 40 (20 a 30 membros cada) |
| Data | 2026-08-26 |

**Leia a latência de rede antes de qualquer outro número.** Todo tempo abaixo já
inclui o piso de ~8.5 ms de rede; o que o benchmark mede é o **incremento** sobre
esse piso, que é a parte atribuível ao `$graphLookup`.

> Uma rodada anterior foi medida com um proxy no caminho e piso de **256 ms**. Ele
> dominava tudo: a expansão de profundidade 3 aparecia como "275 ms" quando o
> trabalho real era cerca de 1 ms. Duas conclusões daquela rodada eram artefato de
> rede e foram corrigidas — a principal é que a poda **não** deixava a query mais
> lenta, isso era ruído. Vale como lembrete: medir do lado errado da rede mede a
> rede.

## Otimização de índice para o caminho com poda

`restrictSearchWithMatch: {weight: {$lte: N}}` é o padrão da UI. Com `{from: 1}`
sozinho, esse filtro roda no estágio **FETCH**: o servidor busca o documento e só
então descarta. Medido num nó de grau 400 no grafo de escala:

| Índice | Devolvidos | Chaves examinadas | Docs examinados |
|---|---|---|---|
| `{from: 1}` | 227 | 400 | **400** |
| `{from: 1, weight: 1}` | 227 | 227 | **227** |

Efeito no tempo, mesmo nó:

| | `{from: 1}` | `{from: 1, weight: 1}` | |
|---|---|---|---|
| profundidade 1 | 81,0 ms | **58,9 ms** | **−27%** |
| profundidade 2 | 3.311 ms | 3.461 ms | +5% (ruído) |

**Leitura honesta:** o composto ajuda o traversal raso e podado, que é o caso da
tela. Em profundidade maior o custo é montar o resultado, não buscar chave, e o
índice não muda nada. Custo: 26,4 MB numa coleção de 2,4 M de arestas, cerca de
1%. Vale a pena, e não é a diferença entre viável e inviável — essa é a poda.

Reproduzir: `.venv/bin/python tests/index_tuning.py`

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
| 1 | 9.2 | 11.0 | 7 |
| 2 | 9.1 | 10.0 | 15 |
| 3 | 9.5 | 10.4 | 30 |
| 4 | 10.0 | 12.1 | 30 |
| 5 | 11.0 | 13.1 | 30 |

## `$graphLookup` — Padrão A (atributo implícito via `device_id`)

| Profundidade | Média (ms) | p95 (ms) | Contas alcançadas |
|---|---|---|---|
| 1 | 9.4 | 11.7 | 2 |
| 2 | 11.9 | 42.1 | 2 |
| 3 | 10.9 | 29.2 | 2 |

O Padrão A devolve **transações**, não pessoas. Converter em nós de pessoa
exigiria os `$lookup` que o Padrão B já pagou uma vez, no job de materialização.
Comparar os dois tempos diretamente é injusto com o Padrão B: ele entrega mais.

## Impacto de `restrictSearchWithMatch` (poda de hubs)

| Cenário (profundidade 4) | Média (ms) | Nós retornados |
|---|---|---|
| sem `restrictSearchWithMatch` | 11.9 | 30 |
| com poda por peso (`weight <= 50`) | 10.3 | 30 |

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
> o BFS nunca faz trabalho pesado. Esta seção responde "e com o meu volume?" com
> número.

**Grafo de teste:** anexação preferencial sobre as mesmas 150 mil pessoas,
**2.398.814 arestas dirigidas**, cauda longa de grau com teto de 400. Coleção
`connections_scale`, indexada em `from` e `to`.

| Entrada | Profundidade | Nós alcançados | Arestas | Tempo |
|---|---|---|---|---|
| grau 340 | 1 | 10.668 | 11.894 | **110 ms** |
| grau 340 | 2 | — | — | ✗ **estoura 100 MB após 38 s** |
| grau 14 | 1 | 626 | 642 | 12,6 ms |
| grau 14 | 2 | 21.278 | 25.809 | **254 ms** |
| grau 14 | 3 | — | — | ✗ estoura 100 MB após 180 s |
| grau 12 | 1 | 363 | 376 | 11,8 ms |
| grau 12 | 2 | 13.597 | 15.823 | **153 ms** |
| grau 12 | 3 | — | — | ✗ estoura 100 MB após 76 s |

**Dois saltos são interativos mesmo alcançando 21 mil nós** — 254 ms. Três saltos
não existem sem poda: o traversal alcança a maior parte do componente e estoura o
teto de 100 MB do documento de saída (`LIMITATIONS.md §5`), depois de moer entre
38 e 180 segundos.

### A poda é o que torna a query possível

Mesma entrada de grau alto, mesma profundidade 1, com e sem
`restrictSearchWithMatch`:

| | Nós | Tempo |
|---|---|---|
| sem poda | 10.668 | 104 ms |
| **com poda** (`weight <= 5`) | 3.620 | **45 ms** |

E numa medição anterior, com um nó de grau 18 na profundidade 3: **sem poda o
traversal estourava os 100 MB após 40,7 s; com poda, 33.872 nós em 1.044 ms.** De
impossível para um segundo.

`restrictSearchWithMatch` poda **durante** o traversal, antes de o array de saída
crescer. Um `$match` depois do `$graphLookup` não salvaria nada — o estágio já
teria estourado.

### O que estes números dizem, e o que não dizem

**Dizem:** num grafo de 2,4 M de arestas num M20 compartilhado, traversal de 1 a 2
saltos é interativo (12–254 ms) para qualquer perfil de grau, e a profundidade 3
exige poda para existir. Investigação de fraude é um caso de 1 a 3 saltos, então
isso cobre o caso de uso.

**Não dizem** nada sobre bilhões de arestas, grafo shardado (`LIMITATIONS.md §1`)
ou algoritmos contínuos (`§3`). E o teto de 100 MB (`§5`) é estrutural: não some
com tier maior.

**O que mudaria:** mais RAM reduz o tempo dos casos que cabem, mas não levanta o
teto. Para componentes maiores que isso, a resposta é pré-computar subgrafos, não
comprar máquina.

## Conclusões honestas

**Sobre a rede da demo, o traversal é praticamente de graça.** Piso de rede 8,5 ms;
profundidade 1 custa 9,2 ms e profundidade 5 custa 11,0 ms. O trabalho atribuível
ao `$graphLookup` vai de ~0,7 ms a ~2,5 ms. A curva é plana porque a rede satura em
30 nós na profundidade 3.

Isso prova que o traversal encontra a rede sem custo perceptível, e **não prova
nada sobre volume** — para isso, a seção *Escala*.

**A poda ajuda, e a medição anterior dizia o contrário por causa da rede.** Com
piso de 256 ms, "273 ms sem poda contra 284 ms com" era ruído interpretado como
sinal. Com piso de 8,5 ms: 11,9 ms sem poda contra 10,3 ms com, na profundidade 4.
E no grafo de escala a diferença é de outra ordem: 104 ms contra 45 ms, ou
"impossível" contra "1 segundo" na profundidade 3.

**A poda que mais importa continua sendo a da materialização.**
`materialize_connections.py --report-only` mostra o que ela descarta:

| Tipo de hub | Grupos descartados | Alcance |
|---|---|---|
| endereço | 3 | 8.618 pessoas |
| dispositivo | 5 | 35.251 contas |

Sem esse descarte, essas 8.618 pessoas ficariam mutuamente conectadas em um salto.

**Padrão A e Padrão B não são comparáveis pelo relógio.** Os dois ficam em ~10 ms
porque os dois são triviais nesta escala; o Padrão A alcança 2 contas e o Padrão B
alcança 30 nós de pessoa. A razão para preferir o Padrão B na investigação não é
velocidade: é `restrictSearchWithMatch`, que só existe com a aresta materializada.

**O que ainda pode ser otimizado:** o índice composto `{from: 1, weight: 1}` (seção
acima, −27% no caminho podado) já está aplicado. Além disso, o que resta é
arquitetura, não tuning: expansão sob demanda em vez de vizinhança inteira, e
pré-computação de subgrafos para componentes que não cabem no teto de 100 MB.
