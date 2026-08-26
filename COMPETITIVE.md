# MongoDB comparado a bancos de grafo dedicados

> Regra de uso deste documento: **nunca citar número de performance de mercado
> sem benchmark próprio**. Toda afirmação de "mais rápido" ou "mais barato"
> precisa vir com o contexto do cenário testado. Os números observados neste projeto
> estão em [`queries/benchmarks.md`](queries/benchmarks.md), com o script que os
> reproduz.

## A objeção que este projeto responde

> "MongoDB não é um banco de grafo. Para esse caso de uso eu preciso de Neo4j (ou
> Neptune) ao lado."

A objeção é **parcialmente verdadeira**, e a resposta correta não é negá-la — é
qualificá-la. Verdadeira para cargas *graph-first* com algoritmos pesados
contínuos. Falsa para a maioria dos casos enterprise, onde grafo é uma **lente de
investigação sobre dados que já são operacionais no MongoDB**.

## Tabela comparativa

| Dimensão | MongoDB (`$graphLookup`) | Neo4j | Amazon Neptune |
|---|---|---|---|
| Modelo primário | documento, com traversal via aggregation pipeline | grafo nativo (nós/arestas com propriedades) | grafo nativo (property graph via Gremlin/openCypher, ou RDF via SPARQL) |
| Linguagem de query | Aggregation Pipeline (`$graphLookup`) | Cypher | Gremlin / openCypher / SPARQL |
| Algoritmos de grafo built-in | **não** — requer camada externa (Spark, NetworkX) | sim (Graph Data Science: PageRank, Louvain, centralidade) | parcial (Neptune Analytics cobre um subconjunto) |
| Casamento de padrão de grafo | traversal BFS, não pattern matching declarativo | sim, é o ponto forte do Cypher | sim |
| OLTP no mesmo sistema | sim — é o sistema de registro nativo | não recomendado como OLTP primário; costuma conviver com outro banco | tipicamente não é o sistema operacional primário |
| Full-text search no mesmo motor | sim (Atlas Search, Lucene embarcado) | não nativo | não nativo |
| Vector search no mesmo motor | sim (Atlas Vector Search) | não nativo | não nativo |
| Transação ACID multi-documento | sim | sim | limitado |
| Sharding do dado operacional | sim, com a ressalva de `$graphLookup` sobre coleção não-shardada (`LIMITATIONS.md §1`) | limitado — Fabric tem trade-offs de consistência entre shards | sim, com trade-offs próprios de particionamento de grafo |
| Superfícies operacionais a manter | 1 sistema | tipicamente 2+ (grafo + operacional) | tipicamente 2+ |
| Melhor encaixe | traversal moderado sobre dados que já vivem no MongoDB: investigação, fraude, compliance, hierarquias, recomendação simples | grafo como caso de uso central e contínuo, exploração ad-hoc profunda, algoritmos pesados em produção | AWS-native, RDF/SPARQL como requisito, grafos massivos com Gremlin |

## O argumento de custo total, com cuidado

O argumento que costuma ressoar mais do que performance bruta é **redução de
superfície operacional**. Um segundo banco dedicado a grafo implica:

- segunda camada de backup e DR;
- segunda camada de observabilidade;
- segunda competência de time (Cypher ou Gremlin);
- segunda superfície de segurança e compliance — relevante em FSI (LGPD, BACEN);
- **um pipeline de sincronização entre os dois sistemas**, que é ele próprio uma
  fonte de bugs e de divergência de dados.

Quando o caso de uso é "investigação sobre dados operacionais" — o mais comum em
FSI/fraude, telecom/topologia de rede e RH/hierarquias — manter isso no MongoDB
elimina esse pipeline inteiro: o dado nunca sai do sistema de registro.

**Isso não é gratuito.** Para cargas graph-first pesadas, o custo de não ter
algoritmos nativos (`LIMITATIONS.md §3`) pode superar a economia de TCO. A conversa
correta é dimensionar caso a caso, não assumir a resposta.

## O que este projeto mostra na prática

Todos os quatro no mesmo cluster, sobre a mesma coleção operacional:

1. traversal multi-hop com poda de hub (`$graphLookup` + `restrictSearchWithMatch`);
2. entity resolution difusa que a igualdade exata não pega (Atlas Search `fuzzy`);
3. similaridade semântica em texto livre (Atlas Vector Search);
4. ação atômica sobre a rede inteira (transação ACID multi-documento) e alerta em
   tempo real na próxima transação que a toca (Change Streams).

Os itens 2, 3 e 4 são o argumento mais forte, e não são sobre grafo: são sobre o
que você **não precisa integrar** para que a investigação seja completa.

## Quando um banco de grafo ao lado é a recomendação certa

- Algoritmos pesados (PageRank, detecção de comunidade) precisam rodar
  **continuamente em produção** sobre a base inteira, não em batch ou amostra.
- O caso de uso é *graph-first* desde a concepção — um produto cuja proposta de
  valor é exploração de grafo ad-hoc pelo usuário final.
- O time já tem profundidade em Cypher/Gremlin e o custo de troca de skillset
  supera o ganho de simplificação operacional.

Nesses casos o padrão recomendado é **co-existência**, não substituição: MongoDB
como sistema operacional de registro, com sincronização via Kafka ou Spark
Connector para o banco de grafo dedicado.

## Perguntas a fazer antes de assumir qualquer posição

1. O grafo é o produto, ou é uma lente de investigação sobre um produto que já
   existe (pagamentos, cadastro, rede de telecom)?
2. Os algoritmos precisam rodar em tempo real sobre 100% da base, ou uma amostra
   ou subgrafo relevante já resolve o problema de negócio?
3. Qual a profundidade de traversal necessária no caso real? Um a dois saltos é
   um problema muito diferente de seis ou mais.
4. O time já opera Cypher/Gremlin em produção, ou seria uma competência a
   construir do zero?

**São as respostas a estas quatro perguntas — não a tabela acima isoladamente —
que determinam a recomendação.**
