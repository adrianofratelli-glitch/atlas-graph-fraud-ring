# What this project does not solve

Read this before presenting it to anyone.

The idea here is to show that you can investigate a graph inside the database
where the data already lives. It is not a claim that MongoDB replaces a graph
database in every situation, because it does not.

Raising these limitations yourself, mid-conversation, tends to work better than
hoping nobody asks. That is why the script in `docs/demo-script.md` already brings
up cost at step 5, before the close.

## 1. `$graphLookup` and sharding

`$graphLookup` does not read in a distributed way from a sharded collection. The
collection it traverses has to be unsharded. That is an architectural constraint,
not a setting you tune.

**How to work around it:**
- Keep the traversal collections (`connections`, and the entity collection)
  unsharded, even when the very high volume operational collections
  (`transactions`) are sharded. That is exactly the split this project uses: the
  graph lives in `connections`, materialised from `transactions`.
- For entity graphs that will not fit in an unsharded collection with acceptable
  performance, the right pattern is to **pre-compute** the relevant communities and
  subgraphs (Atlas Data Federation, Spark Connector, a batch job) and materialise
  the result into a smaller collection optimised for traversal.

## 2. There is no graph query language

MongoDB has no equivalent to Cypher or Gremlin. `$graphLookup` does traversal
(BFS from an entry point), not graph pattern matching — no "find all triangles",
no optimised generic shortest path between two arbitrary nodes.

**What this project offers instead:** `/api/hops` computes the distance between two
entities with `$graphLookup` plus `depthField`, which answers the question a fraud
investigation actually asks ("how many hops separate A from B"). The API response
itself carries a `caveat` field saying this is not optimised shortest path. If that
distinction matters for the customer's use case, it will surface during
qualification — better that it surfaces now.

## 3. There are no graph algorithms on the server

MongoDB has no native implementations of PageRank, community detection (Louvain,
Label Propagation) or centrality (betweenness, closeness). Neo4j offers those via
Graph Data Science; Neptune, partially, via Neptune Analytics.

**The path when that is needed:** MongoDB as the operational system of record,
exported via the Spark Connector (or the Kafka Connector, for streaming),
processed in Spark GraphX or NetworkX/igraph **over a relevant subgraph, never
over the whole dataset**, with the scores and clusters written back into MongoDB as
document attributes and consumed normally by the application.

**When a dedicated graph database is the right answer:** *graph-first* workloads,
with heavy algorithms running continuously in production over the entire base — a
recommendation engine recalculating PageRank in real time over billions of edges,
for example. In that scenario the honest answer is that Neo4j and Neptune have a
structural advantage.

## 4. Cost grows with the number of neighbours, not with depth

`$graphLookup` is an iterative BFS: each level fires new lookups. Cost grows with
the **fan-out reached**, not with the number in `maxDepth`.

### What the fraud ring shows

Entering through the leader of a 30-member ring, on the default dataset:

| Depth | Nodes reached | Ring members | Behaviour |
|---|---|---|---|
| 1 | 7 | 7/30 | a handful of direct links |
| 2 | 16 | 16/30 | half the ring |
| 3 | 31 | **30/30** | the ring closes |
| 4 | 31 | 30/30 | saturated |
| 5 | 31 | 30/30 | saturated |

Note what does **not** happen: from depth 3 on, the traversal saturates. The ring
is a closed component, and adding depth reaches nobody new. Times per depth are in
[`queries/benchmarks.md`](queries/benchmarks.md), measured over 30 runs per row
discarding warm-up.

**Do not use this case to talk about degradation** — it proves the opposite. If you
tell a customer that depth 5 is expensive and the screen shows the same 31 nodes in
the same time, the demo contradicts the presenter.

### Where degradation actually shows up

At a hub. Measured on this dataset, with `queries/07_hub_fanout.js`:

| | |
|---|---|
| Most shared device | operated by **7,125** distinct accounts |
| Pattern A, a single hop from it | 7,125 accounts, 7,236 transactions, **1,499 ms** cold (304 ms warm) |

One hop. Thousands of accounts with no relationship whatsoever to each other. That
is the cost pruning avoids — and it is why the pruning that matters happens **at
materialisation**, not at query time.

`materialize_connections.py --report-only` shows what it discards:

| Hub type | Groups discarded | Reach |
|---|---|---|
| address | 3 | 8,618 people |
| device | 5 | 35,251 accounts |

Without that discard, those 8,618 people would be mutually connected one hop apart.

### The two mitigation layers

1. **At materialisation** (`materialize_connections.py`): an attribute only becomes
   an edge if its fan-out is below `HUB_FANOUT_THRESHOLD`. A bank branch's address
   is not evidence of a link — materialising it would turn half the database into a
   single connected component.
2. **At query time** (`restrictSearchWithMatch`): pruning by edge type and edge
   weight, exposed in the UI as a toggle.

**Measured: on this dataset query-time pruning makes the query slower** — it has
nothing left to remove, because materialisation already removed it, and the extra
filter is pure cost. The toggle stays on screen because in a real database, where
edges arrive ready-made from an upstream system, it becomes the primary control
again — but here it is the second line of defence, not the first. Saying that is
more convincing than pretending pruning always helps.

### The edge-type toggle tells you which link holds the ring together

The three checkboxes are not decoration. On the default entry point, at depth 3:

| Edges enabled | Nodes reached |
|---|---|
| all three | 31 |
| without destination PIX key | 31 |
| without address | 30 |
| **without device** | **3** |

The device fingerprint is what holds this ring together; address and destination
key are reinforcement. That is a hypothesis an analyst can test live, in front of
the customer, without writing a query.

## 5. The 100 MB ceiling on the `$graphLookup` result

This is the most important limitation on the list, and it showed up through
measurement.

`$graphLookup` accumulates the entire traversal into a single array (`as:
"network"`) inside **one** document. An intermediate aggregation document is
capped at 100 MB, and the stage blows up when the traversal is large:

```
Executor error during aggregate command ... caused by ::
Total size of the output document exceeds 104857600 bytes.
Consider using $unwind to split the output.
```

Measured in `tests/scale_graph.py`, over a graph of **2.4 million directed edges**
(preferential attachment, 150,000 nodes):

| Entry | Depth | Result |
|---|---|---|
| degree 340 | 1 | 10,668 nodes, 11,894 edges, **110 ms** |
| degree 340 | 2 | **fails** — exceeds 100 MB after 38 s |
| degree 14 | 2 | 21,278 nodes, 25,809 edges, **254 ms** |
| degree 14 | 3 | **fails** — exceeds 100 MB after 180 s |
| degree 18 | 3 | **fails** — exceeds 100 MB after 41 s |
| degree 18 | 3, **with pruning** | 33,872 nodes, 45,318 edges, **1,044 ms** |

Look at the pair in the last two rows: the same question is impossible without
pruning and costs one second with it.

And look at the time **before** the failure — 38 to 180 seconds. The stage does not
fail fast: it traverses, accumulates, and only discovers it does not fit once the
time is already spent. That is why the backend passes `maxTimeMS`
(`GRAPH_MAX_TIME_MS`, default 15 s) — without it the client stares at a frozen
screen for up to three minutes to receive an error.

**`allowDiskUse` does not help.** This is not a sort or group spill: it is the size
of a single document. The ceiling is structural.

**The backend's `GRAPH_MAX_NODES` does not protect you**, and `GRAPH_MAX_TIME_MS`
only limits the damage. Truncation happens in application code, after the stage has
already built the whole array on the server. A client asking for depth 3 on a large
graph gets an error from Mongo, not a truncated result — the backend translates it
into a 503, but the traversal did not happen.

### What actually protects you

1. **`restrictSearchWithMatch`**, which prunes *during* the traversal, before the
   array grows. Measured: the same traversal that blows up after 41 s without
   pruning finishes in **1,044 ms** with it; and at depth 1 from a degree-340 node,
   104 ms without pruning against 45 ms with. It is not a cosmetic filter — it is
   what makes the query possible, and it is the reason the toggle exists in the UI.
   A `$match` **after** `$graphLookup` would save nothing: the stage would already
   have blown up.
2. **Low depth** with on-demand expansion: expand one node at a time from the
   previous result, instead of asking for the whole neighbourhood at once.
3. **Pre-computed subgraphs** (§1) when the relevant component is too large to fit
   in a single traversal.

### What to tell the customer

That a ceiling exists, what it is, and how it is worked around. An architect who
discovers this limit alone, after buying into the idea, loses confidence in
everything said before it. An architect who hears it during the demo understands
that the project was actually measured.

The honest comparison: a dedicated graph database does not have this specific
ceiling — it streams the result instead of materialising a document. In exchange,
`$graphLookup` runs inside the same transaction and the same cluster as the
operational data. It is a real trade-off, and it should be presented as one.

## 6. The demo graph is small, and that is the strongest objection

Best to say so before anyone asks. The injected rings have 30 members and form
closed components; traversing them costs a few milliseconds of real work. That
proves `$graphLookup` **finds** the network. It proves nothing about cost, because
the search never does heavy work.

Someone who works with ten million entities and hears "30 nodes in 9 milliseconds"
is right not to be impressed: the number does not speak to their problem.

That is why `tests/scale_graph.py` exists. It builds a preferential-attachment
graph of **~2.4 million directed edges** over the same 150,000 people and measures
traversal from high, medium and low degree nodes. The numbers are in
[`queries/benchmarks.md`](queries/benchmarks.md), section *Scale*. Use those, not
the ring's, when the conversation is about volume.

What remains out of scope even with the scale test: billions of edges, a sharded
graph (§1) and heavy continuous algorithms (§3).

## 7. Selective filters in vector search have a ceiling of their own

Scoping the semantic panel to the ring on screen uses `ring_id`, which is already a
filter field on the vector index. But `$vectorSearch` walks the HNSW graph of the
**entire collection** and applies the filter during the walk. A very selective
filter — 137 transactions of one ring inside 604,000 — discards almost every
candidate, and with a low `numCandidates` the search runs out before it has
gathered results.

Measured: with `numCandidates: 360` the ring returned **one** reason; the data held
nine. Raising it to 10,000 returns all of them — and 10,000 is the server's
ceiling (`"numCandidates" must be within bounds [1..10000]`), not a number someone
picked. That also bounds how selective a filter can usefully be here.

## 8. The data is synthetic, and the topology was designed on purpose

Yes. It is documented in `docs/adr/0001-topologia-do-dado-sintetico.md`, including
the three alternatives discarded by measurement and why. What the topology does
**not** do is hide degradation: that shows up in the table above and on screen, at
step 5 of the script.

What synthetic data does not prove: the fan-out distribution of a real database,
the noise of real customer-record quality, and the behaviour of genuinely free-text
`reason_text` (here it comes from a pool of templates — see
`docs/adr/0002-vetores-em-512d-quantizados.md`).

## 9. The scope of this project

Traversal and investigation over a moderately sized entity graph: 150,000 people,
~240,000 accounts, 604,000 transactions, ~119,000 materialised edges — plus a
synthetic 2.4 M edge graph used only to measure scale. It is **not** evidence of
behaviour at billions of edges with heavy algorithms running continuously; for that
scenario, see section 3.

The demo cluster is an **M20 shared with other applications**. That shapes
technical decisions: the vectors are 512-dimensional, stored as BinData float32,
with a quantised index (see `docs/adr/0002-vetores-em-512d-quantizados.md`).
Sizing for production would start somewhere else.
