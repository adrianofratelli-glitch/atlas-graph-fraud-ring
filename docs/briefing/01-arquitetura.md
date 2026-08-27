# 01 — Architecture

## Layers

```
┌────────────────────────┐   ┌────────────────────────┐   ┌─────────────────────────┐
│ React + Vite (5350)     │──▶│ FastAPI (8350)          │──▶│ MongoDB Atlas            │
│ vis-network             │◀──│ app/db  · data access   │◀──│ $graphLookup             │
│ EventSource (SSE)       │   │ app/services · orch.    │   │ Atlas Search             │
│ shared design tokens    │   │ AlertHub (thread)       │   │ Atlas Vector Search      │
└────────────────────────┘   └────────────────────────┘   │ Change Streams           │
                                                            │ ACID transaction         │
                                                            └─────────────────────────┘
                                                                        ▲
                                                             ┌──────────┴──────────┐
                                                             │ data-generator/      │
                                                             │ idempotent           │
                                                             └─────────────────────┘
```

Layering rule: **no route imports `pymongo`**. `main.py` only exposes HTTP and
translates exceptions into status codes; every query lives in `backend/app/db/`.
That is what makes it possible to change driver or version without touching the
routes.

## Invariants

1. **Deterministic `_id` on all generated data.** `det_id(kind, *parts)` is a
   `uuid5` over the key attributes. Running the generator twice rewrites the same
   documents — no write uses a random `ObjectId`.
2. **The backend decides the depth, not the frontend.** `clamp_depth()` caps at
   `GRAPH_MAX_DEPTH_CAP` (default 6). An uncapped expansion is the fastest way to
   freeze a live demo.
3. **Retry only for transient network failure.** `with_retry()` retries
   `AutoReconnect`, `NetworkTimeout` and `ConnectionFailure` with exponential
   backoff, at most 3 times. A logic or validation error is never retried —
   retrying hides bugs.
4. **Degradation per feature, not per screen.** A missing or `BUILDING`
   Search/Vector index becomes a `503` with `{feature, index, status}`; the
   frontend shows a badge on that panel and the traversal keeps working.
5. **Traceable ground truth.** Every injected network carries `ring_id` on
   `people`, `accounts` and `transactions`, plus a summary in `rings`. The demo
   never depends on randomness having cooperated.
6. **Ceiling on returned nodes.** `GRAPH_MAX_NODES` (default 1200) truncates by
   shortest hop distance. At high depth the bottleneck is the browser, not Atlas;
   truncating from the periphery preserves what matters, and the payload reports
   `truncated: true` so the UI can say so out loud.
7. **A time ceiling, not just a size ceiling.** Every traversal runs with
   `maxTimeMS` (`GRAPH_MAX_TIME_MS`, default 15 s). Measured on a 2.4 M edge graph,
   `$graphLookup` ground for **97 seconds** before exceeding the 100 MB
   output-document limit. Without the ceiling that is a frozen screen for a minute
   and a half ending in an error. With it, the route returns `503` with
   `too_large: true`, the reason, and what to do about it.
8. **POST bodies go through a schema, not manual checks.** The `pydantic` models at
   the top of `main.py` exist because two real bugs came from hand-rolled
   validation: `limit: -1` reached `$vectorSearch` and became a 500, and
   `person_ids` as a string was iterated character by character until the
   transaction aborted with a 409.

## Environment variables

| Variable | Default | Role |
|---|---|---|
| `MONGODB_URI` | — | required |
| `MONGODB_DB` | `graph_fraud_ring` | the project's database |
| `ATLAS_SEARCH_INDEX_NAME` | `people_entity_resolution` | entity resolution |
| `VECTOR_INDEX_NAME` | `transactions_reason_vector` | semantic similarity |
| `VOYAGE_API_KEY` | — | without it, Vector Search degrades with `NO_EMBEDDING_KEY` |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `voyage-3-lite` / `512` | see ADR 0002 |
| `GRAPH_MAX_DEPTH_CAP` | `6` | absolute depth ceiling |
| `GRAPH_DEFAULT_DEPTH` | `4` | used when the client sends no depth |
| `HUB_FANOUT_THRESHOLD` | `50` | hub pruning threshold |
| `GRAPH_MAX_NODES` | `1200` | node ceiling in the payload |
| `GRAPH_MAX_TIME_MS` | `15000` | time ceiling for the traversal aggregation |

## How to run

```bash
cp .env.example .env      # fill in MONGODB_URI and VOYAGE_API_KEY

# dependencies
python3 -m venv .venv && .venv/bin/pip install -r data-generator/requirements.txt
python3 -m venv backend/venv && backend/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)

# data (idempotent; ~20 min for the default volume)
bash data-generator/run_all.sh
.venv/bin/python schema/search_indexes.py       # waits until READY
.venv/bin/python data-generator/embed_reasons.py

# start
./start.sh                 # POV_DEV=1 ./start.sh for HMR and --reload
```

`embed_reasons.py` runs **last**, after the rings are injected. Run it earlier and
the ring transactions end up without vectors, and the semantic panel scoped to a
network comes back almost empty.

## Order of work

Data, indexes, `mongosh` queries, backend, frontend, benchmarks. The topology of
the synthetic data was decided by measurement before a single line of backend
existed — without that, the demo shows either one whole clique at depth 1, or half
the database at depth 2. See `docs/adr/0001-topologia-do-dado-sintetico.md`.

## Endpoints

| Method | Route | Role |
|---|---|---|
| GET | `/health/live` | pure liveness, does not touch the database |
| GET | `/health` | deep check: connection, counts, search indexes, reference `$graphLookup` latency, change stream state |
| GET | `/api/entry-points` | demo entry points, from the ground truth in `rings` |
| GET | `/api/network/{person_id}` | Pattern B — expansion over explicit edges (`depth`, `edge_types`, `prune_hubs`) |
| GET | `/api/network-by-device/{account_id}` | Pattern A — traversal by `device_id`, for comparison |
| GET | `/api/hops` | hop distance between two people |
| GET | `/api/search/people` | Atlas Search with `fuzzy` + `autocomplete`, unscoped |
| POST | `/api/search/people` | the same search, aware of the graph on screen: scopes and/or annotates each result |
| POST | `/api/search/similar-reasons` | Vector Search over `reason_embedding`, grouped by text, optionally scoped to a ring |
| POST | `/api/investigation/flag` | multi-document ACID transaction; refuses a second case over nodes already in an open one |
| GET | `/api/investigation/case/{case_id}` | the open case with the before/after of the accounts the transaction changed |
| GET | `/api/investigation/coaf/{case_id}` | structured suspicious-activity report, built from what the transaction already wrote |
| POST | `/api/investigation/close/{case_id}` | closes a case |
| POST | `/api/demo/reset` | returns the dataset to its pre-demo state |
| POST | `/api/demo/simulate-transaction` | injects a transaction into the network on screen, with or without it being flagged |
| POST | `/api/demo/link-accounts` | inserts transactions that make two unlinked people share a device; the change stream is what creates the edge |
| GET | `/api/connections/between` | is there an edge between two people? used to prove the before and after |
| GET | `/api/alerts/stream` | SSE fed by the change stream |
| GET | `/api/alerts/recent` | the most recent persisted alerts |

## Traps found while building

Recorded because they cost time and would come back in any similar project:

- **`shares_device` from both ends of the transaction.** "A paid B from A's phone"
  is not a shared device. Including `to_account` in the materialisation took the
  average degree of the clean population from ~1 to 8.6, and depth 2 started
  sweeping half the database.
- **The misspelled twin colliding with the next ring.** An off-by-one in
  recruitment made the twin of ring *r* the leader of ring *r+1*, and the
  misspelled name overwrote the leader's — breaking the correspondence step 7 of
  the script depends on. The symptom was subtle: the demo worked, it just pointed
  at the wrong person.
- **Identity character swap in the typo generator.** `"e" -> "e"` produced a twin
  with an exactly identical name, which Atlas Search resolves by exact match — the
  `fuzzy` clause was never exercised.
- **`connections` out of date with respect to the member set.** Re-injecting the
  networks without re-materialising the edges left ~3% of members with no edge. One
  of those picked as an entry point opens an empty graph in front of the customer;
  that is why `_entry_nodes` only chooses nodes with confirmed edges.
- **`ObjectId` in the alert payload.** SSE serialises to JSON; `alerts._id` became a
  string derived from the transaction's `_id`, and the write became an upsert so
  that an `update` on the same transaction does not kill the listener thread with a
  duplicate key.
- **`depthField` returns a `NumberLong`.** In `mongosh`, `long + 1` concatenates a
  string instead of adding. The scripts use `$toInt`.
- **A destination-key edge that turned the ring into a clique.** Spreading the
  funnel across five fixed collectors made every payer of a key a clique member,
  and cliques of six to seven collapsed the ring's diameter: entering from any node
  brought 22 of 30 members at the first hop. Paying the branch parent instead makes
  the payers of a key exactly the *siblings* of that branch — groups of four, and
  the reveal by depth survives.
- **A batch write drowning the change stream.** The listener reacted to *any* write
  on `transactions`, so an embedding backfill (`update_many` per text) fired
  thousands of edge materialisations and as many SSE events, with no device or
  account having changed. It now acts only on `insert`, or on an `update` whose
  `updatedFields` touches `device_id`, `from_account` or `to_account`. Worth
  knowing before a demo: after a large batch, the listener spends time draining the
  oplog from its stored resume token, and new events only appear once it catches up.

## Tests

```bash
.venv/bin/python tests/test_resilience.py           # full hostile suite
.venv/bin/python tests/test_resilience.py --quick   # without change stream and load
.venv/bin/python tests/scale_graph.py --build       # scale graph (slow)
.venv/bin/python tests/scale_graph.py --measure     # measures traversal at scale
.venv/bin/python tests/index_tuning.py              # gain from the compound index
mongosh "$MONGODB_URI" queries/08_explain_traversal.js   # IXSCAN proof
```

`tests/test_resilience.py` is not a unit test. Each case corresponds to a real way
this project could fail in front of a customer, and the pass criterion is to
**degrade with a clear message**: never a 500, never a hang, never inconsistent
data. The blocks:

| Block | What it tries to break |
|---|---|
| Hostile input | giant id, non-existent id, depth 999 and negative, invented edge type, Mongo operator in `_id`, empty and 5000-character searches, regex metacharacters, out-of-range `limit`, wrongly typed `person_ids` |
| Payload consistency | edge pointing at a missing node, divergent counts, more than one root, depth monotonicity, pruning that increases reach, non-determinism |
| ACID transaction | repeated flagging, non-existent id, above the ceiling, **8 concurrent transactions over the same documents** |
| Execution plan | missing traversal indexes and disguised COLLSCAN |
| Change Streams | burst of 12 simultaneous transactions, lost event, listener death |
| Edge maintenance | link created live by the change stream, duplication on reprocessing |
| Load | 40 concurrent expansions with 10 workers, p50/p95 |

Real bugs that came out of it, all fixed:

- `limit: -1` reached `$vectorSearch` and became a 500;
- `person_ids` as a string was iterated character by character until the
  transaction aborted with a 409;
- the pair of people in `link-accounts` was fixed, so the **second consecutive
  presentation** failed: the two were already linked by the first. It is now drawn
  at random, and `POST /api/demo/reset` removes the edges created live;
- flagging twice overwrote `case_id` on the accounts and left the first case as a
  shell with `status: "open"` and zero accounts. The endpoint now refuses, and
  returns the open `case_id` so the screen can offer to close it.

The first two became schema validation (`pydantic`) instead of manual checks.

`tests/scale_graph.py` and `tests/index_tuning.py` do not test: they measure. The
first builds a 2.4 million edge graph to find where the traversal stops fitting;
the second compares `{from: 1}` with `{from: 1, weight: 1}` on the pruned path.
Both write JSON alongside, and the numbers go into `queries/benchmarks.md`.
