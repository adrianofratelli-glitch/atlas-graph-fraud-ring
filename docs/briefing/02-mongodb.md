# 02 — MongoDB

The detailed modelling, including the reasoning behind the choice between the two
graph patterns, is in [`schema/collections.md`](../../schema/collections.md). This
file covers what runs against the cluster.

## Collections

| Collection | Role | Source |
|---|---|---|
| `people` | graph entities | `generate_synthetic_data.py` |
| `accounts` | accounts, 1-3 per person, each with a unique PIX key | idem |
| `devices` | one per account plus 5 deliberate hubs | idem |
| `transactions` | 600k, with `reason_text`, `reason_embedding` and `to_pix_key` | idem + `embed_reasons.py` |
| `connections` | materialised edges, bidirectional | `materialize_connections.py` |
| `rings` | ground truth of the injected networks | `inject_fraud_rings.py` |
| `investigations` | cases opened by the ACID transaction | backend |
| `alerts` | change stream fires | backend |
| `connections_scale` | synthetic 2.4 M edge graph, only for measuring scale | `tests/scale_graph.py` |

## Indexes

> **The trap that cost the most here.** `materialize_connections.py --rebuild`
> calls `drop()` on `connections`, and `drop()` takes the indexes with it. Because
> the pipeline created the indexes in an earlier step, the traversal ran a
> **COLLSCAN of 117,974 documents at every BFS level** — and nothing broke, it just
> got slow. The first published benchmarks were measured that way.
>
> Fix: the `connections` indexes are created **by the materialisation itself**, at
> the end of it, and `run_all.sh` checks they exist before declaring success.
> `queries/08_explain_traversal.js` and the *Execution plan* block of
> `tests/test_resilience.py` are the regression guard.
>
> Measured effect of the fix at depth 3: **525 ms → 275 ms**, against a network
> floor of 256 ms at the time. Measured later without a proxy in the path, with an
> 8.5 ms floor, the same query came in at **9.5 ms**.

`schema/indexes.js` creates the B-trees for the other collections;
`schema/search_indexes.py` creates the search indexes, which have their own
lifecycle (`PENDING` → `BUILDING` → `READY`) and therefore do not fit in the same
script.

The four `connections` indexes, and why each exists:

| Index | Role |
|---|---|
| `{from: 1}` | `connectFromField` of `$graphLookup` |
| `{to: 1}` | `connectToField`; read at every level to build the next batch |
| `{type: 1, from: 1}` | filter by edge type, which is the screen's toggle |
| `{from: 1, weight: 1}` | pruning filter inside the index, not in FETCH |

The last one was added after measuring. With `{from: 1}` alone,
`restrictSearchWithMatch: {weight: {$lte: N}}` runs at the **FETCH** stage: the
server fetches the document and only then discards it. On a degree-400 node in the
scale graph, that was 400 documents read to return 227. With the compound index,
227 keys and 227 documents, and single-hop traversal dropped 27%.

At greater depth the compound index changes nothing, because there the cost is
assembling the result, not fetching keys. It costs about 1% of the collection size.
See `tests/index_tuning.py`.

Three more indexes exist for reasons that only showed up in operation:

| Index | Why |
|---|---|
| `accounts.pix_key` (unique, sparse) | the DICT guarantees one key per account; the unique index is the guard against the wrong model creeping back in |
| `transactions.simulated` (sparse) | `POST /api/demo/reset` deletes simulated transactions; without it that is a COLLSCAN of 604k documents and the reset times out mid-presentation |
| `transactions.{reason_text, reason_embedding}` | `embed_reasons.py` does one `update_many` per distinct text filtering on a missing embedding; without it that is 33 COLLSCANs of 600k documents |

## Edge materialisation: the rule that matters

An attribute only becomes an edge if its fan-out is **below
`HUB_FANOUT_THRESHOLD`** (default 50). A device used by 800 accounts, or a bank
branch's address, is not evidence of a link — materialising it would turn half the
database into a single connected component.

`shares_device` is built only from `from_account`, deliberately. Including
`to_account` would turn "A paid B from A's phone" into a shared-device edge, which
is no link at all. It was measured: with `to_account` included, the average degree
of the clean population rises from ~1 to 8.6 and depth 2 sweeps half the database.

### The PIX key: the model that was corrected

The first version linked two people by the **same PIX key**, with `pix_key` on
`people`. That does not exist in a Brazilian bank: the DICT directory (BCB
Resolution No. 1/2020) guarantees that one key addresses exactly one transactional
account at a time and rejects duplicate registration. The edge described a state
the payment system prevents — the kind of error that costs the credibility of the
entire conversation at the architect's first question.

The current model:

- `accounts.pix_key` is **unique per account**, with a `unique` index. A CPF key on
  account `k=0` (one per person), a random key (EVP) on the others, as the DICT
  allows.
- `transactions.to_pix_key` holds the payment's **destination** key.
- `same_pix_counterparty` groups by `to_pix_key` and links the people whose
  accounts paid into the same key. That is the collection account at the end of the
  mule funnel.

Two guards against legitimate noise, in `pix_counterparty_groups`: the payer must
have paid that key at least `MIN_PIX_HITS` times (a single payment to a merchant is
a purchase, not complicity), and a key receiving from more than
`HUB_FANOUT_THRESHOLD` people is a hub and does not become an edge.

### The funnel topology, and why it is hierarchical

Each member pays the **parent** in the tree, which is their branch's collector. The
first version spread payments across five fixed collectors, and the effect on the
topology was bad: the payers of one key become a clique at materialisation, and
cliques of six to seven collapsed the ring's diameter. Entering from any node
brought 22 of 30 members at the first hop — the reveal by depth, which is the spine
of the demonstration, stopped existing.

Paying the parent makes the payers of a key exactly the **siblings** of that
branch: groups of four, and the PIX edge links sibling to sibling. It complements
the device edge (which links parent to child) instead of duplicating it, and the
tree stays deep. The measured reveal from the leader is 7 → 16 → 31 nodes.

`data-generator/migrate_pix_model.py` migrates an already-generated dataset without
regenerating the 150,000 people. The `to_pix_key` backfill runs entirely on the
server, with `$lookup` + `$merge`: the naive path (one `update_many` per key) would
be 240,000 round trips to the cluster. It is resumable — it filters on what still
has no key — which mattered in practice, because the first run was interrupted with
part of the `$merge` already committed.

## Pipelines

| File | What it demonstrates |
|---|---|
| `queries/01_graphlookup_explicit_edges.js` | Pattern B, time by depth |
| `queries/02_graphlookup_shared_attributes.js` | Pattern A, for comparison |
| `queries/03_hops_between.js` | hop distance via `depthField` |
| `queries/04_prune_impact.js` | measured impact of `restrictSearchWithMatch` |
| `queries/05_search_and_vector.js` | fuzzy entity resolution |
| `queries/06_transaction_flag_ring.js` | multi-document ACID transaction |
| `queries/bench.py` | fills `queries/benchmarks.md` with measured numbers |

## ACID transaction

Scope: `accounts.status`, `people.risk_flags` and one document in `investigations`.
`readConcern: snapshot`, `writeConcern: majority`. Either the whole network goes
under investigation or none of it does — an intermediate state (half the accounts
blocked, with no coherent audit record) is worse than not having acted.

The endpoint refuses above 5000 nodes: a network that size is batch-job work, not a
single transaction.

It also refuses to open a **second case over nodes already in an open one**.
Without that, flagging twice overwrote `case_id` on the accounts and the first case
became a shell: `status: "open"` in `investigations`, zero accounts pointing at it.
Closing the second released everything, leaving an audit record in the database
claiming an open investigation over nothing. For a project whose argument is the
coherence of the record, that state is worse than an error — and refusing is also
the correct banking behaviour.

## Incremental edge maintenance

The batch job takes ~15 minutes. In production the graph cannot wait for it: the
same change stream that fires the alert also materialises the edge
(`app/services/edge_maintenance.py`). A new transaction arrives, the device link
appears in `connections` in **~2 s**, with `source: "change_stream"`, and the next
`$graphLookup` already walks it.

The batch rules are preserved literally — deterministic `_id` in the same
namespace, `from_account` only, a hub above the threshold does not become an edge.
If they diverge, the incremental graph and the rebuilt graph stop being the same
graph.

Maintenance only acts on `insert`, or on an `update` whose `updatedFields` touches
`device_id`, `from_account` or `to_account`. Without that guard any write on
`transactions` reopened the materialisation: an embedding backfill fired thousands
of edge writes and as many SSE events, with no device or account having changed.

What the incremental path does **not** do: prune an edge that stopped making sense,
and backfill a window before the listener started. The correct pattern is both —
change stream for freshness, periodic batch for consistency and pruning. See
`docs/adr/0003-manutencao-incremental-de-arestas.md`.

## Change Streams

`transactions.watch()` on its own thread, with `full_document="updateLookup"`,
`max_await_time_ms` (so it does not block shutdown) and a **stored `resume_token`**,
to pick up where it left off if the cursor drops during a failover. Each SSE
subscriber has a queue capped at 200 events: a slow client must not let the
listener grow memory without a ceiling.

The listener publishes two kinds of event. `ring_touch` is the alert. `checked` is
a simulated transaction that touched **no** flagged account: it reports how many
accounts were checked and in how many milliseconds. That one exists because silence
must not look like a broken demo — injecting into a free network is half the A/B,
and without an event on screen the presenter has no proof the listener even woke
up. It is limited to simulated transactions; publishing it for all 600,000 would
drown the SSE in noise.

## Selective filters in vector search

`ring_id` was already a filter field on the vector index, so scoping the semantic
panel to the network on screen required no index rebuild. It did require
understanding a trap, and that trap is worth the technical conversation.

`$vectorSearch` walks the HNSW graph of the **entire collection** and applies the
filter during the walk. A very selective filter — 137 transactions of one ring
inside 604,000 — discards nearly every candidate, and the search exhausts its list
before gathering results. Measured: with `numCandidates: 360` the ring returned
**one** reason; the data held nine.

`numCandidates: 10000` fixes it, and 10,000 is the **server ceiling**
(`"numCandidates" must be within bounds [1..10000]`), not a number someone picked —
which also bounds how selective a filter can usefully be here.

## Embeddings and pipeline order

`embed_reasons.py` runs after `generate_synthetic_data.py`, but it used to run
**before** `inject_fraud_rings.py`. The result: ring transactions were born without
`reason_embedding`, and the semantic panel scoped to a network returned almost
nothing — 11 of 137 vectorised transactions in one ring.

The script is idempotent and only fills what is missing, so the fix is to run it
**after** ring injection and after any migration that creates transactions.
`run_all.sh` already does the right order; anyone migrating existing data has to
remember.

## Seeds and reproducibility

Fixed seed (`20260826`) in the generator and in `Faker`. Deterministic `_id`
everywhere. Running the pipeline again produces exactly the same dataset —
including the same rings for the demo script.
