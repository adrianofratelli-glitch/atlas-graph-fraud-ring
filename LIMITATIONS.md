# What this project does not solve

Read this before presenting it to anyone.

The idea here is to show that you can investigate a graph inside the database
where the data already lives. It is not a claim that MongoDB replaces a graph
database in every situation, because it does not.

Raising these limitations yourself, mid-conversation, tends to work better than
hoping nobody asks. That is why the script in `docs/demo-script.md` already brings
up cost at step 5, before the close.

## 0. The scope this project chose, before any technical limitation

This POV demonstrates one shape of graph workload: a **shallow tree queried by
business key**, loaded in volume. Ownership chains and commercial hierarchies both
have that shape — a handful of levels, a predictable question, an operational read
rate.

It deliberately does **not** try to prove MongoDB replaces a graph database for the
other shape: a dense network explored ad-hoc, with graph algorithms running
continuously. There the structural advantages of a native graph engine —
index-free adjacency, embedded algorithms — are real, and the honest answer is
co-existence. The table under "Why this shape of graph, and not another" in
`README.md` is the filter that separates the two.

Everything below is a limitation *within* the scope this project did choose.

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

### What the ownership chain shows

Entering through the applicant of a showcase group, on the default dataset:

| Depth | Companies | Shareholders | Consolidated limit | Overdue |
|---|---|---|---|---|
| 1 | 5 | 6 | R$ 11.8 M | **R$ 0** |
| 2 | **25** | 12 | **R$ 197.2 M** | **R$ 11.2 M** |
| 3 | 25 | 13 | R$ 197.2 M | R$ 11.2 M |
| 6 (cap) | 25 | 13 | R$ 197.2 M | R$ 11.2 M |

Two things to read here, and the second is the honest one.

**The reveal is real.** At depth 1 the group looks clean; deeper, the arrears
appear in a branch the applicant is not part of. Each showcase group has its own
ownership depth, from 1 to 6 levels, so the control keeps revealing new companies
right up to that group's floor.

**Past a group's own depth, nothing changes.** The tree is closed, so extra levels
reach nobody new — and those rows cost the same. Do **not** use this to talk about
degradation: if you tell a customer depth 6 is expensive while the screen shows the
same companies in the same time, the demo contradicts the presenter. Depth costs 5
to 8 ms of real work across the whole 1-to-6 range; the shape of the tree decides
the answer, not the number in the control.

That is not a weakness of the measurement. It is the defining property of the
pattern this POV was built for, and it is exactly why a dedicated graph engine
would win nothing here.

### Where the cost actually shows up

Not in depth — in how much the aggregation has to sum afterwards:

| Scope | Advisors reached | Accounts with credit | p50 |
|---|---|---|---|
| advisor | 1 | 421 | 18 ms |
| manager | 16 | 6,317 | 43 ms |
| regional | 129 | 50,989 | **462 ms** |

The traversal is trivial at every row — three hops down a 969-person tree. What
grows is the `$group` over tens of thousands of exposure documents. Above the
regional level the honest answer is a rollup, not a live query, and saying so is
better than letting a customer find the cliff during their own POC.

### The mitigation that mattered, and the one that did not

**Mattered: fewer round trips.** The same answer went from up to ten serial calls
(52 ms) to one aggregation (13 ms). On a shallow tree that is the whole game.

**Mattered: summing the right collection.** Denormalising `advisor_id` onto
`credit_exposure` took the regional book from 13,010 ms to 462 ms, by removing a
per-document join rather than by adding an index.

**Mattered: indexing the right collection.** Moving the activity vectors from
`companies` (1.2 M rows, 32 distinct texts) to `activities` (32 rows) took the
concentration query from 29.3 s to milliseconds. See §7.

**Did not matter: a compound index on the edge.** `{owned_id: 1, owner_type: 1}`
only pays off with `restrictSearchWithMatch` filtering on `owner_type`, and the
traversal deliberately does not filter that way — cutting individual shareholders
would cut exactly the shareholder who bridges two groups the registry treats as
unrelated. The index was measured, gained nothing for this access pattern, and was
not kept. Reporting that is more useful than pretending every index helps.

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

Measured on a **dense** graph of 2.4 million directed edges (preferential
attachment over 150,000 nodes), built by an earlier version of this project when
the use case was a fraud network. That test was removed along with the fraud model,
so these rows are history rather than something you can re-run here — kept because
the ceiling is a property of `$graphLookup`, not of that dataset, and because it is
the limitation most likely to bite a customer who applies this pattern to a dense
graph:

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

**On the current dataset none of this happens**, and that is the honest reading:
the ownership chain is shallow and closed, the largest showcase group returns 25
companies, and the traversal never comes close to the ceiling. The limitation is
real and it is simply not the regime this POV operates in — which is exactly the
distinction the qualification table in `README.md` draws.

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

## 6. The demo graph is shallow, and someone will call that convenient

Best to say so before anyone asks. The showcase groups run 1 to 6 levels and 7 to
43 companies, and traversing the deepest costs 8 ms of real work. That proves
`$graphLookup` **answers** the question. It proves nothing about a deep or dense
graph, because the search never does heavy work.

The difference from the usual version of this objection is that the shallowness is
not a shortcut — it is the **hypothesis**. This POV claims that a large class of
enterprise graph problems is shallow and point-queried, and that for those the
right question is load throughput and operational cost, not traversal speed. The
qualification table in `README.md` is what decides whether a given customer is in
that class.

So the answer to "your graph is small" is not a bigger graph. It is: *is your real
access pattern this shape?* If it is not — if it is ad-hoc exploration with
algorithms running continuously — then §3 applies and the honest recommendation is
co-existence.

What genuinely remains out of scope: billions of edges, a sharded graph (§1), and
continuous graph algorithms (§3).

## 7. Vector search punishes indexing the wrong collection

The concentration panel compares the meaning of activity descriptions. The first
version embedded `cnae_descricao` on all 1.2 M companies and ran `$vectorSearch`
over `companies` with a selective filter.

That runs into a real property of the engine: `$vectorSearch` walks the HNSW graph
of the **entire collection** and applies the filter during the walk. A very
selective filter discards nearly every candidate, so the search exhausts its list
before gathering results — the panel comes back almost empty with nothing to
explain why. Raising `numCandidates` fixes the emptiness and costs time; 10,000 is
the server ceiling (`"numCandidates" must be within bounds [1..10000]`), not a
number someone picked, which also bounds how selective a filter can usefully be.

Measured that way: **29.3 seconds** per query.

The real fix was not a bigger `numCandidates`. The base has **32 distinct activity
descriptions**, and the comparison is between activities, not between the 1.2 M
companies that repeat them. The vectors now live in their own `activities`
collection, one document per description, and the same answer takes milliseconds.

The limitation to state honestly is therefore about design, not about the engine:
**a vector index over a collection whose rows repeat the same text is the wrong
index**, and the cost of getting that wrong grows with the size of the collection
rather than with the size of the problem.

## 8. The data is synthetic, and the topology was designed on purpose

Yes. It is documented in `docs/adr/0001-topologia-do-dado-sintetico.md`, including
the alternatives discarded by measurement and why.

Two design choices worth admitting out loud, because a careful architect will spot
both:

- **The population of individuals is sized to make the data behave, not to match a
  registry.** With 1.2 M companies, ~2.2 shareholders each and 150,000 people, the
  same person lands in ~17 companies and "shared shareholder" becomes a property of
  every pair of companies rather than a finding. The population is 800,000 with a
  long-tailed draw, and 15% of companies draw from a narrow band that plays the
  recurring-shareholder role.
- **The bridge shareholder and the cross-holdings are planted.** They exist so the
  demo has something to find. A real registry has them too, but at a rate nobody in
  this project measured.

What synthetic data does not prove: the fan-out distribution of a real ownership
registry, the noise of real record quality, and behaviour on genuinely free-text
activity descriptions (here they come from a pool of CNAE templates — see
`docs/adr/0002-vetores-em-512d-quantizados.md`).

Group **detection quality** is also unproven, and it is a different claim from the
one this project makes: the groups are generated ground truth, so precision and
recall against a real registry were never measured. This demonstrates the query,
not a data-quality pipeline over public CNPJ data.

## 9. Analytical scope has a ceiling, and the app refuses instead of queueing

The consolidated book of a regional — 129 advisors, ~51,000 credit exposures —
takes about 460 ms on its own and does not get cheaper with more clients asking
for it. Measured at 64 concurrent clients without protection: p95 of **8.8
seconds** across the whole application, including the point lookups that cost 4 ms
of database work.

The application now caps concurrency per class of query and returns **429** to what
does not fit (`app/services/limits.py`). That keeps the interactive path at a p95
of ~308 ms, and it is the honest behaviour — but it *is* a ceiling, and it should
be stated as one:

- above a few concurrent analytical queries, some callers get refused;
- for a workload where many users pull large consolidated books at once, the
  answer is a **rollup**, not this query;
- the ceiling here is a shared M20 and a demo dataset. Sizing for production is a
  separate conversation, not an extrapolation of these numbers.

## 10. The scope of this project

An ownership graph of 1.2 M companies, 2.5 M directed ownership edges, 800,000
individual shareholders and 385,000 credit exposures, plus a 969-person commercial
hierarchy. It is **not** evidence of behaviour at billions of edges with heavy
algorithms running continuously; for that scenario, see section 3.

The demo cluster is an **M20 shared with other applications**. That shapes technical
decisions: the vectors are 512-dimensional, stored as BinData float32, with a
quantised index (see `docs/adr/0002-vetores-em-512d-quantizados.md`). Sizing for
production would start somewhere else.

Load throughput was measured **from a workstation against a remote cluster**, so
client network latency is included and dominates. A production load runs in the
cluster's own region. Present that number as a floor observed under bad conditions,
never as a ceiling.
