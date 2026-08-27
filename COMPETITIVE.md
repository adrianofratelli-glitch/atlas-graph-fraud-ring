# MongoDB compared to dedicated graph databases

> Rule for using this document: **never quote a market performance number without
> your own benchmark**. Every claim of "faster" or "cheaper" has to come with the
> context of the scenario tested. The numbers observed in this project are in
> [`queries/benchmarks.md`](queries/benchmarks.md), along with the script that
> reproduces them.

## The objection this project answers

> "MongoDB is not a graph database. For this use case I need Neo4j (or Neptune)
> alongside it."

The objection is **partly true**, and the right response is not to deny it — it is
to qualify it. True for *graph-first* workloads with heavy continuous algorithms.
False for most enterprise cases, where the graph is an **investigative lens over
data that is already operational in MongoDB**.

## Comparison table

| Dimension | MongoDB (`$graphLookup`) | Neo4j | Amazon Neptune |
|---|---|---|---|
| Primary model | document, with traversal via the aggregation pipeline | native graph (nodes/edges with properties) | native graph (property graph via Gremlin/openCypher, or RDF via SPARQL) |
| Query language | Aggregation Pipeline (`$graphLookup`) | Cypher | Gremlin / openCypher / SPARQL |
| Built-in graph algorithms | **no** — requires an external layer (Spark, NetworkX) | yes (Graph Data Science: PageRank, Louvain, centrality) | partial (Neptune Analytics covers a subset) |
| Graph pattern matching | BFS traversal, not declarative pattern matching | yes, it is Cypher's strong point | yes |
| OLTP in the same system | yes — it is the native system of record | not recommended as primary OLTP; usually sits next to another database | typically not the primary operational system |
| Full-text search in the same engine | yes (Atlas Search, embedded Lucene) | not native | not native |
| Vector search in the same engine | yes (Atlas Vector Search) | not native | not native |
| Multi-document ACID transaction | yes | yes | limited |
| Sharding of the operational data | yes, with the caveat that `$graphLookup` needs an unsharded collection (`LIMITATIONS.md §1`) | limited — Fabric has cross-shard consistency trade-offs | yes, with its own graph partitioning trade-offs |
| Operational surfaces to maintain | 1 system | typically 2+ (graph + operational) | typically 2+ |
| Best fit | moderate traversal over data already living in MongoDB: investigation, fraud, compliance, hierarchies, simple recommendation | graph as the central, continuous use case; deep ad-hoc exploration; heavy algorithms in production | AWS-native, RDF/SPARQL as a requirement, massive graphs with Gremlin |

## The total cost argument, handled carefully

The argument that usually resonates more than raw performance is **reduced
operational surface**. A second database dedicated to the graph implies:

- a second backup and DR layer;
- a second observability layer;
- a second team skill set (Cypher or Gremlin);
- a second security and compliance surface — material in financial services
  (LGPD, central bank rules);
- **a synchronisation pipeline between the two systems**, which is itself a source
  of bugs and data divergence.

When the use case is "investigation over operational data" — the most common one in
financial services and fraud, telecom network topology, and HR hierarchies —
keeping it in MongoDB removes that entire pipeline: the data never leaves the
system of record.

**This is not free.** For heavy graph-first workloads, the cost of having no native
algorithms (`LIMITATIONS.md §3`) can outweigh the TCO saving. The right
conversation is to size it case by case, not to assume the answer.

## What this project shows in practice

All four on the same cluster, over the same operational collection:

1. multi-hop traversal with hub pruning (`$graphLookup` + `restrictSearchWithMatch`);
2. fuzzy entity resolution that exact equality misses (Atlas Search `fuzzy`);
3. semantic similarity over free text (Atlas Vector Search);
4. atomic action over the whole network (multi-document ACID transaction) and a
   real-time alert on the next transaction that touches it (Change Streams).

Items 2, 3 and 4 are the stronger argument, and they are not about graphs: they are
about what you **do not have to integrate** for the investigation to be complete.

## When a graph database alongside is the right recommendation

- Heavy algorithms (PageRank, community detection) need to run **continuously in
  production** over the entire base, not in batch or on a sample.
- The use case is *graph-first* by design — a product whose value proposition is
  ad-hoc graph exploration by the end user.
- The team already has depth in Cypher/Gremlin, and the cost of switching skill
  sets outweighs the operational simplification.

In those cases the recommended pattern is **co-existence**, not replacement:
MongoDB as the operational system of record, synchronised via Kafka or the Spark
Connector into the dedicated graph database.

## Questions to ask before taking any position

1. Is the graph the product, or is it an investigative lens over a product that
   already exists (payments, customer records, a telecom network)?
2. Do the algorithms need to run in real time over 100% of the base, or does a
   sample or relevant subgraph already solve the business problem?
3. What traversal depth does the real case need? One or two hops is a very
   different problem from six or more.
4. Does the team already run Cypher/Gremlin in production, or would that be a skill
   built from scratch?

**It is the answers to these four questions — not the table above on its own —
that determine the recommendation.**
