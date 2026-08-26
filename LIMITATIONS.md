# O que este projeto não resolve

Leia antes de apresentar para alguém.

A ideia aqui é mostrar que dá para investigar um grafo dentro do banco onde os
dados já estão. Não é dizer que o MongoDB substitui um banco de grafo em toda
situação, porque não substitui.

Trazer estas limitações você mesmo, no meio da conversa, costuma funcionar melhor
do que esperar que ninguém pergunte. O roteiro em `docs/demo-script.md` já traz a
questão de custo no passo 5, antes do fechamento, por isso.

## 1. `$graphLookup` e sharding

O `$graphLookup` não lê de forma distribuída de uma coleção shardada. A coleção
que ele percorre precisa ser não-shardada. Isso é uma restrição de arquitetura,
não uma configuração que se ajusta.

**Como contornar:**
- Isolar as coleções de traversal (`connections`, e a coleção de entidades) como
  não-shardadas, mesmo que as coleções operacionais de altíssimo volume
  (`transactions`) sejam shardadas. É exatamente a separação que este projeto usa:
  o grafo vive em `connections`, materializada a partir de `transactions`.
- Para grafos de entidades que não cabem em uma coleção não-shardada com
  performance aceitável, o padrão correto é **pré-computar** comunidades e
  subgrafos relevantes (Atlas Data Federation, Spark Connector, job em batch) e
  materializar o resultado numa coleção menor, otimizada para traversal.

## 2. Não existe uma linguagem de consulta de grafo

MongoDB não tem equivalente a Cypher ou Gremlin. `$graphLookup` faz traversal
(BFS a partir de um ponto de entrada), não casamento de padrão de grafo — nada de
"encontre todos os triângulos" ou shortest-path genérico otimizado entre dois nós
arbitrários.

**O que este projeto oferece no lugar:** `/api/hops` calcula distância entre duas
entidades com `$graphLookup` + `depthField`, o que resolve a pergunta que a
investigação de fraude realmente faz ("quantos saltos separam A de B"). A própria
resposta da API carrega o campo `caveat` dizendo que isso não é shortest-path
otimizado. Se essa distinção importa para o caso do cliente, ela vai aparecer na
qualificação — melhor que apareça agora.

## 3. Não existem algoritmos de grafo no servidor

Não existem no MongoDB implementações nativas de PageRank, detecção de comunidade
(Louvain, Label Propagation) ou centralidade (betweenness, closeness). Neo4j
oferece isso via Graph Data Science; Neptune, parcialmente via Neptune Analytics.

**O caminho quando isso é necessário:** MongoDB como sistema operacional de
registro → export via Spark Connector (ou Kafka Connector, em streaming) →
processamento em Spark GraphX ou NetworkX/igraph **sobre um subgrafo relevante,
nunca sobre o dataset inteiro** → scores e clusters escritos de volta no MongoDB
como atributo do documento, consumidos normalmente pela aplicação.

**Quando um banco de grafo dedicado é a resposta certa:** cargas *graph-first*, com
algoritmos pesados rodando continuamente em produção sobre a base inteira (um
motor de recomendação com PageRank recalculado em tempo real sobre bilhões de
arestas, por exemplo). Nesse cenário a resposta honesta é que Neo4j e Neptune têm
vantagem estrutural.

## 4. O custo cresce com o número de vizinhos, e não com a profundidade

`$graphLookup` é um BFS iterativo: cada nível dispara novas buscas. O custo cresce
com o **fan-out alcançado**, não com o número em `maxDepth`.

### O que a rede de fraude mostra

Entrando pelo líder de uma rede de 30 membros, no dataset padrão (piso de rede
8,5 ms):

| Profundidade | Nós alcançados | Membros da rede | Tempo (ms) |
|---|---|---|---|
| 1 | 7 | 7/30 | 9,2 |
| 2 | 15 | 15/30 | 9,1 |
| 3 | 30 | **30/30** | 9,5 |
| 4 | 30 | 30/30 | 10,0 |
| 5 | 30 | 30/30 | 11,0 |

Repare no que **não** acontece: a partir da profundidade 3 o traversal satura. A
rede é um componente fechado, e aumentar a profundidade não alcança mais ninguém.
(30 execuções por linha, descartando o aquecimento; ver
[`queries/benchmarks.md`](queries/benchmarks.md).)

**Não use este caso para falar de degradação** — ele prova o contrário. Se disser
ao cliente que a profundidade 5 é cara e a tela mostrar 30 nós em 11 ms, a demo
desmente o apresentador.

### Onde a degradação realmente aparece

Num hub. Medido neste dataset, com `queries/07_hub_fanout.js`:

| | |
|---|---|
| Dispositivo mais compartilhado | operado por **7.125 contas** distintas |
| Padrão A, um único salto a partir dele | 7.125 contas, 7.236 transações, **3.204 ms** a frio (356 ms com cache quente) |

Um salto. Milhares de contas sem relação nenhuma entre si. É esse o custo que a
poda evita — e é por isso que a poda que importa acontece **na materialização**,
não em tempo de query.

`materialize_connections.py --report-only` mostra o que ela descarta:

| Tipo de hub | Grupos descartados | Alcance |
|---|---|---|
| endereço | 3 | 8.618 pessoas |
| dispositivo | 5 | 35.251 contas |

Sem esse descarte, essas 8.618 pessoas ficariam mutuamente conectadas em um salto.

### As duas camadas de mitigação

1. **Na materialização** (`materialize_connections.py`): um atributo só vira
   aresta se o fan-out estiver abaixo de `HUB_FANOUT_THRESHOLD`. O endereço de uma
   agência bancária não é evidência de vínculo — materializá-lo transformaria
   metade da base num único componente conexo.
2. **Em tempo de query** (`restrictSearchWithMatch`): poda por tipo e por peso de
   aresta, exposta na UI como um toggle.

**Medido: neste dataset a poda em tempo de query deixa a query mais lenta** — 633
ms com poda contra 530 ms sem, devolvendo os mesmos 28 nós. Ela não tem nada a
remover, porque a materialização já removeu, e o filtro extra é custo puro. O
toggle continua na tela porque em uma base real, onde as arestas chegam prontas de
um sistema anterior, ele volta a ser o controle principal — mas aqui é a segunda
linha de defesa, não a primeira. Dizer isso é mais convincente do que fingir que a
poda sempre ajuda.

## 5. O teto de 100 MB no resultado do `$graphLookup`

Esta é a limitação mais importante da lista, e ela apareceu medindo.

`$graphLookup` acumula o traversal inteiro num único array (`as: "network"`)
dentro de **um** documento. Documento intermediário de agregação tem limite de
100 MB, e o estágio estoura quando o traversal é grande:

```
Executor error during aggregate command ... caused by ::
Total size of the output document exceeds 104857600 bytes.
Consider using $unwind to split the output.
```

Medido em `tests/scale_graph.py`, sobre um grafo de **2,4 milhões de arestas
dirigidas** (anexação preferencial, 150 mil nós), entrando por um nó de grau 398:

| Entrada | Profundidade | Resultado |
|---|---|---|
| grau 340 | 1 | 10.668 nós, 11.894 arestas, **110 ms** |
| grau 340 | 2 | **falha** — estoura os 100 MB após 38 s |
| grau 14 | 2 | 21.278 nós, 25.809 arestas, **254 ms** |
| grau 14 | 3 | **falha** — estoura os 100 MB após 180 s |
| grau 18 | 3 | **falha** — estoura os 100 MB após 41 s |
| grau 18 | 3, **com poda** | 33.872 nós, 45.318 arestas, **1.044 ms** |

Repare no par das duas últimas linhas: a mesma pergunta é impossível sem poda e
custa um segundo com poda.

E repare no tempo **antes** da falha — de 38 a 180 segundos. O estágio não falha rápido: ele
percorre, acumula, e só descobre que não cabe quando já gastou o tempo. Por isso o
backend passa `maxTimeMS` (`GRAPH_MAX_TIME_MS`, padrão 15 s) — sem ele, o cliente
olha uma tela parada por até três minutos para receber um erro.

**`allowDiskUse` não ajuda.** Não é spill de sort ou de group: é o tamanho de um
único documento. O teto é estrutural.

**`GRAPH_MAX_NODES` do backend não protege**, e `GRAPH_MAX_TIME_MS` só limita o
prejuízo. O truncamento acontece no
código da aplicação, depois que o estágio já montou o array inteiro no servidor.
Um cliente que peça profundidade 3 num grafo grande recebe erro do Mongo, não um
resultado truncado — o backend traduz em 503, mas o traversal não aconteceu.

### O que realmente protege

1. **`restrictSearchWithMatch`**, que poda *durante* o traversal, antes de o array
   crescer. Medido: o mesmo traversal que estoura em 41 s sem poda termina em
   **1.044 ms** com poda; e na profundidade 1 a partir de um nó de grau 340,
   104 ms sem poda contra 45 ms com. Não é um filtro cosmético — é o que torna a query
   possível, e é a razão de o toggle existir na UI. Um `$match` **depois** do
   `$graphLookup` não salvaria nada: o estágio já teria estourado.
2. **Profundidade baixa** com expansão sob demanda: expandir um nó por vez a
   partir do resultado anterior, em vez de pedir a vizinhança inteira de uma vez.
3. **Pré-computar subgrafos** (§1) quando o componente relevante é grande demais
   para caber num traversal só.

### O que dizer ao cliente

Que existe um teto, qual é, e como se contorna. Um arquiteto que descobre esse
limite sozinho, depois de comprar a ideia, perde a confiança em tudo que foi dito
antes. Um arquiteto que ouve isso na demo entende que o projeto foi medida de verdade.

A comparação honesta: um banco de grafo dedicado não tem esse teto específico —
ele transmite o resultado em vez de materializar um documento. Em compensação,
`$graphLookup` roda dentro da mesma transação e do mesmo cluster do dado
operacional. É um trade-off real, e é assim que deve ser apresentado.

## 6. O grafo da demo é pequeno, e essa é a objeção mais forte

Convém dizer isso antes que perguntem. As redes injetadas têm 30 membros e formam
componentes fechados; o traversal sobre elas custa ~20 ms de trabalho real, contra
um piso de 256 ms de rede. Isso prova que o `$graphLookup` **encontra** a rede.
Não prova nada sobre custo, porque o BFS nunca faz trabalho pesado.

Um arquiteto que ouve "525 ms para 30 nós" e trabalha com 10 milhões de entidades
tem razão em não se impressionar.

Por isso existe `tests/scale_graph.py`, que constrói um grafo de anexação
preferencial de **~2,4 milhões de arestas dirigidas** sobre as mesmas 150 mil
pessoas e mede o traversal a partir de nós de grau alto, médio e baixo. Os números
estão em [`queries/benchmarks.md`](queries/benchmarks.md), seção *Escala*. Use
esses, não os do anel, quando a conversa for sobre volume.

O que continua fora do escopo mesmo com o teste de escala: bilhões de arestas,
grafo shardado (§1) e algoritmos pesados contínuos (§3).

## 7. Os dados são sintéticos, e a topologia foi desenhada de propósito

Sim. Está documentado em `docs/adr/0001-topologia-do-dado-sintetico.md`, inclusive
as três alternativas descartadas por medição e por quê. O que a topologia **não**
faz é esconder a degradação: ela aparece na tabela acima e na tela, no passo 5 do
roteiro.

O que o dado sintético não prova: distribuição de fan-out de uma base real, ruído
de qualidade de cadastro, e o comportamento de `reason_text` de campo livre
verdadeiro (aqui ele vem de um pool de templates — ver
`docs/adr/0002-vetores-em-512d-quantizados.md`).

## 8. O escopo deste projeto

Traversal e investigação sobre um grafo de entidades de tamanho moderado: 150 mil
pessoas, ~240 mil contas, 600 mil transações, ~118 mil arestas materializadas —
mais um grafo sintético de 2,4 M de arestas usado só para medir escala.
**Não** é evidência de comportamento em bilhões de arestas com algoritmos pesados
rodando continuamente — para esse cenário, ver a seção 3.

O cluster da demo é um **M20 compartilhado com outras aplicações**. Isso molda
decisões técnicas: os vetores são de 512 dimensões,
gravados como BinData float32, com índice quantizado (ver
`docs/adr/0002-vetores-em-512d-quantizados.md`). Um dimensionamento para produção
partiria de outro lugar.
