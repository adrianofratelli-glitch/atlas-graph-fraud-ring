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
5. **Traceable ground truth.** Every showcase group is recorded in
   `economic_groups` — holding, members, applicant, distressed sibling, bridge
   shareholder and cross-holdings. The demo never depends on randomness having
   cooperated, and never scans the base live in front of the customer.
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
8. **The server decides the visibility scope.** The frontend sends *who the user
   is*; `app/db/hierarchy.py` derives what that user reaches by walking down
   `advisors.reports_to`. It never accepts a list of ids from the client — the same
   rule as the traversal depth, applied to an access control.
9. **One aggregation per answer, where the tree is shallow.** `economic_group`
   walks up, derives the group roots in the pipeline, walks down from every root in
   a correlated `$lookup` and hydrates in three more. The earlier version did the
   same work in up to ten serial calls and measured 4× slower. On a shallow tree,
   round trips cost more than traversal.
10. **Concurrency is capped per class of query, and the excess is refused.**
   `app/services/limits.py` gives the advisor book four simultaneous slots and the
   concentration analysis two; anything that cannot get a slot in 750 ms gets a
   `429` with `Retry-After`. Measured: without it, 64 concurrent clients took the
   p95 of the *interactive* path from 308 ms to 2 s, because one analytical query
   sat in the same queue. Under saturation an honest system refuses early.
11. **POST bodies go through a schema, not manual checks.** The `pydantic` models at
   the top of `main.py` exist because two real bugs came from hand-rolled
   validation: `limit: -1` reached `$vectorSearch` and became a 500, and
   `person_ids` as a string was iterated character by character until the
   transaction aborted with a 409.

## Environment variables

| Variable | Default | Role |
|---|---|---|
| `MONGODB_URI` | — | required |
| `MONGODB_DB` | `graph_grupo_economico` | the project's database |
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
.venv/bin/python data-generator/embed_activities.py

# start
./start.sh                 # POV_DEV=1 ./start.sh for HMR and --reload
```

`embed_activities.py` runs **last**, after the ownership base exists. Run it
earlier and the group's companies end up without vectors, and the concentration
panel scoped to the group on screen comes back almost empty.

## Order of work

Data, indexes, backend, frontend, benchmarks. The topology of the synthetic data
was decided by measurement before a single line of backend existed — without that,
the demo either shows the whole group at depth 1 or a bare triangle at depth 3.
See `docs/adr/0001-topologia-do-dado-sintetico.md`.

## Endpoints

| Method | Route | Role |
|---|---|---|
| GET | `/health/live` | pure liveness, does not touch the database |
| GET | `/health` | deep check: connection, counts, search indexes, reference `$graphLookup` latency, change stream state |
| GET | `/api/entry-points` | demo entry points, from the ground truth in `economic_groups` |
| GET | `/api/group/{cnpj}` | **the main path**: ownership chain and consolidated exposure, one aggregation (`depth`) |
| POST | `/api/search/companies` | Atlas Search over company and partner names, aware of the group on screen |
| POST | `/api/analysis/concentration` | Vector Search over activity descriptions: how many distinct businesses hide behind N CNAE codes |
| GET | `/api/hierarchy/roster` | example users for the visibility demo |
| GET | `/api/hierarchy/{advisor_id}/portfolio` | scope derived by walking down `reports_to`, plus the consolidated book |
| GET | `/api/hierarchy/{advisor_id}/can-see/{cnpj}` | may this user see this account, and why |
| POST | `/api/credit/review` | multi-document ACID transaction over the whole group; refuses a second case over companies already under review |
| GET | `/api/credit/case/{case_id}` | the open case with the before/after of what the transaction changed |
| POST | `/api/credit/close/{case_id}` | closes a case |
| POST | `/api/demo/reset` | returns the dataset to its pre-demo state |
| POST | `/api/demo/ownership-change` | registers an ownership change on the group on screen, with or without it being under review |
| GET | `/api/alerts/stream` | SSE fed by the change stream |
| GET | `/api/alerts/recent` | the most recent persisted alerts |

## Traps found while building

Recorded because they cost time and would come back in any similar project:

- **A root defined by what the traversal happened to see.** The group roots were
  derived as "companies with no corporate owner *inside the edges walked so far*".
  At depth 1 that promotes a node in the middle of the tree to root, and the
  descent from it returned 19 of the group's 25 companies — the depth control
  looked broken because every level showed the same thing. The fix is to ask the
  database: a root is a company with no corporate owner **in `ownership`**, checked
  with one indexed `$lookup` inside the same aggregation.
- **The opposite version of the same bug.** An earlier attempt discarded any legal
  entity that appeared as *owned*, which excludes the holding itself: walking up
  from the applicant, the holding's individual shareholders produce edges where the
  holding is the owned side. The group came back with two companies instead of
  eight, with no error at all.
- **A deterministic `_id` colliding with itself.** Adding cross-holdings to the
  showcase groups created the pair `(netas[2], bisnetas[11])` — which the level
  loop had already created, because `11 % 9 == 2`. The edge `_id` is a `uuid5` over
  `(owner, owned)`, so the clean load died on `E11000` after 1.9 M inserts. The
  generator now deduplicates the pairs before writing; a deterministic id turns a
  logical duplicate into a load failure, which is the right behaviour but an
  expensive way to learn it.
- **The measurement written after the slow step.** The load report was saved after
  index creation, and index creation timed out — taking with it the throughput
  number that had just been measured over twenty minutes. Persist a measurement
  before anything long enough to fail runs after it.
- **A join per document, hidden behind a small scope.** The advisor book started
  from `companies` in scope and looked up `credit_exposure` per company. For one
  advisor it was invisible; for a regional it was **13 seconds**. Denormalising
  `advisor_id` onto the exposure turned it into an indexed `$match` plus `$group`:
  462 ms.
- **The network floor moving mid-session.** The path to the cluster changed and the
  ping went from 8 ms to **307 ms**. Every scenario "got 25× slower" with no code
  change, and two conclusions from earlier rounds of this project turned out to be
  the same artefact. Record the ping with every measurement; compare increments.
- **Everyone a shared shareholder.** With 1.2 M companies, ~2.2 shareholders each
  and 150,000 individuals, the same person lands in ~17 companies. "Shared
  shareholder" stops being an exception and becomes a property of every pair of
  companies. The population is now 800,000 with a long-tailed draw — the number is
  a modelling decision, not a volume one.
- **`ObjectId` in the alert payload.** SSE serialises to JSON; the alert `_id` is a
  string derived from the source document, and the write is an upsert so that a
  repeated event does not kill the listener thread with a duplicate key.
- **`depthField` returns a `NumberLong`.** In `mongosh`, `long + 1` concatenates a
  string instead of adding. Use `$toInt`.
- **A batch write drowning the change stream.** The listener reacted to *any* write
  on the watched collection, so a backfill fired thousands of events with nothing
  relevant having changed. It now filters on the operation type and the fields that
  actually matter. Worth knowing before a demo: after a large batch the listener
  spends time draining the oplog from its stored resume token, and new events only
  appear once it catches up.

## Tests

```bash
.venv/bin/python tests/test_resilience.py           # full hostile suite
.venv/bin/python tests/test_resilience.py --quick   # without change stream and load
PYTHONPATH=backend .venv/bin/python queries/bench.py --runs 30   # latency, both scenarios
.venv/bin/python tests/stress.py                    # mixed workload, ramps to 64 concurrent
.venv/bin/python tests/stress.py --max 128 --seconds 30
```

`tests/test_resilience.py` is not a unit test. Each case corresponds to a real way
this project could fail in front of a customer, and the pass criterion is to
**degrade with a clear message**: never a 500, never a hang, never inconsistent
data. The blocks:

| Block | What it tries to break |
|---|---|
| Hostile input | giant CNPJ, non-existent CNPJ, depth 999 and negative, Mongo operator in place of an id, empty and 500-character searches, regex metacharacters, out-of-range `limit`, wrongly typed `company_ids` |
| Payload consistency | edge pointing at a missing node, divergent counts, more than one subject, depth monotonicity, non-determinism, consolidated sum equal to the sum of the nodes |
| Hierarchy and scope | manager reaches more than one advisor, advisor reaches only themselves, the manager's book contains the advisor's, the account's own advisor is allowed, **an advisor in another branch is refused**, unknown user is a 404, operator injected in the id is not a query |
| ACID transaction | repeated review, non-existent id, above the ceiling, concurrent transactions over the same documents |
| Execution plan | missing traversal indexes and disguised COLLSCAN |
| Change Streams | burst of simultaneous changes, lost event, listener death |
| Load | concurrent expansions, p50/p95 |

`tests/stress.py` is the other half, and it asks a different question: not "does it
break?" but "what degrades first?". It ramps concurrency, mixes the five demo
paths in realistic proportion, and checks five things — no 5xx, no client-side
connection failures, p95 **per path** under its own ceiling, `/health` still ok
afterwards, and the consolidated figure of a group **unchanged** after the load.
The last one matters most: a number that drifts under concurrency is worse than a
slow one.

Its first run is why the bulkhead exists.

The visibility block is the one worth reading. An implementation that always
authorises passes every "the manager sees more" assertion — so the suite asserts
the refusal explicitly, on an advisor in another branch of the tree.

Real bugs that came out of the suite, all fixed:

- `limit: -1` reached `$vectorSearch` and became a 500;
- a list field arriving as a string was iterated character by character until the
  transaction aborted with a 409;
- reviewing twice overwrote `case_id` and left the first case as a shell with
  `status: "open"` and zero companies. The endpoint now refuses, and returns the
  open `case_id` so the screen can offer to close it.

The first two became schema validation (`pydantic`) instead of manual checks.
