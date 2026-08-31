# Benchmarks

> Numbers measured by `queries/bench.py` against the real cluster. Never replaced
> by an estimate or a market number — the value of this document is that the
> customer can reproduce it with one command.
>
> ```bash
> PYTHONPATH=backend .venv/bin/python queries/bench.py --runs 30
> ```
>
> The first run of each scenario is discarded: it pays for WiredTiger cache
> warm-up and would measure the wrong thing.

## Test environment

| | |
|---|---|
| Cluster tier | M20 (4 GB RAM, 150 GB disk), shared with other applications |
| Network floor to the cluster | **8.5 ms** p50 |
| Volume | 1,200,000 companies · 2,505,533 ownership edges · 800,000 individuals · 384,509 credit exposures · 969 advisors · 32 activities |
| Showcase groups | 40, ownership depths 1 to 6 (7 to 43 companies), disjoint, with cross-holdings and a bridge shareholder |
| Measured | 2026-08-31 |

**Read the network floor before any other number.** Every time below includes it;
what the benchmark measures is the **increment** over the floor, which is the part
attributable to the query.

> This matters more than it sounds. During this round the network path to the
> cluster changed mid-session and the floor went from 8 ms to **307 ms**. Every
> scenario "got 25× slower" without a single line of code changing. Two earlier
> rounds of this project were measured across floors of 256 ms and 324 ms and
> produced two conclusions that were pure network artefact.
>
> If you re-run from a high-latency path, expect every row to shift by the floor.
> Compare increments, record the ping.

## Load throughput — the number that matters first for this pattern

For a shallow tree queried by business key, the customer's bottleneck is not the
traversal. It is loading the base and operating it. `data-generator` writes
`queries/load-results.json` on every clean load. Measured 2026-08-31:

| Collection | Documents | Time | Rate |
|---|---|---|---|
| `companies` | 1,200,000 | 87.2 s | 13,765 docs/s |
| `ownership` | 2,505,533 | 211.4 s | 11,850 docs/s |
| `credit_exposure` | 384,509 | 20.2 s | 19,010 docs/s |
| **total** | **4,090,042** | **318.8 s** | **12,828 docs/s — 46.2 M/hour** |

Measured with `insert_many` in unordered batches of 2,000, **from a workstation
against a shared M20 over the public internet** — client network latency is
included and dominates. A production load runs in the cluster's own region, on a
tier sized for it, and is faster; present this as a floor observed under bad
conditions, never as a ceiling.

The same base loaded at **3,633 docs/s** in an earlier round, on a worse network
path — 3.5× slower with identical code and identical data. That is the third time
in this project that the network, not the database, produced the headline number.
Record the conditions or the number is noise.

## Query latency

### Ownership chain — the main demo path

Each showcase group has its own ownership depth, and each row below is that group
queried **at its own depth** — the traversal reaching the floor of the tree, not
stopping short of it. Network floor for this round: p50 8.5 ms.

| Scenario | Companies reached | p50 | Over floor |
|---|---|---|---|
| 1-level group, depth 1 | 7 | 13.3 ms | **+4.8 ms** |
| 2-level group, depth 2 | 13 | 14.1 ms | +5.6 ms |
| 3-level group, depth 3 | 19 | 15.3 ms | +6.8 ms |
| 4-level group, depth 4 | 25 | 15.3 ms | +6.8 ms |
| 5-level group, depth 5 | 31 | 16.1 ms | +7.6 ms |
| 6-level group, depth 6 | 43 | 16.3 ms | +7.8 ms |
| deepest group, depth 6 (cap) | 43 | 16.6 ms | +8.1 ms |
| company with no group, depth 3 | 1 | 12.2 ms | +3.7 ms |

Six levels of ownership over 1.2 M companies and 2.5 M stakes cost **7.8 ms** of
real database work. Going from one level to six costs 3 ms more — the curve is
flat, and against a 200 ms interactive budget the headroom is roughly 25×.

That headroom is the answer to "how deep can it go?": the cap of 6 is a modelling
choice about what a Brazilian ownership chain actually looks like, not a latency
ceiling. Raising `GRUPO_PROFUNDIDADES` and `GRAPH_MAX_DEPTH_CAP` together and
reloading is all it takes to go further.

Two things this table settles, and the second is the one that gets argued:

- **Depth is not where the cost is.** Each extra level adds well under a
  millisecond, because the traversal is index-driven on `ownership.owner_id` /
  `owned_id` and the tree fans out to tens of nodes, not millions.
- **Asking for more depth than the tree has is free.** The last two rows are the
  same group at its own depth and at the cap: same answer, same latency. The
  traversal ends when the tree ends. Never sell depth 6 as "expensive" — the
  screen will contradict you.

### Commercial hierarchy — visibility scope

| Scenario | Scope | p50 | Over floor |
|---|---|---|---|
| advisor's own book | 1 advisor, 421 accounts with credit | 17.7 ms | +9.2 ms |
| manager's book | 16 advisors, 6,317 accounts with credit | 43.1 ms | +34.6 ms |
| visibility check on one account | — | 9.5 ms | +1.0 ms |

The regional level (129 advisors, ~51,000 accounts with credit) answers in about
**460 ms**. It is the honest ceiling of this shape: the traversal is still trivial,
but the aggregation is summing tens of thousands of documents. Above that, the
answer is a rollup, not a live query — say so before the customer finds it.

## Two optimisations, and what each was worth

Both were found by measuring, and both are more instructive than the absolute
numbers.

### 1. Round trips, not traversal

The first version of the economic-group query did the work in up to **ten serial
calls**: the CNPJ lookup, the upward traversal, one downward traversal *per group
root in a Python loop*, and three hydration finds.

| | Round trips | p50 (8 ms floor) |
|---|---|---|
| serial version | up to 10 | 52 ms |
| single aggregation | **1** | **13 ms** |

Same data, same result, 4× faster. On a shallow tree the traversal costs
single-digit milliseconds, so the response time is decided by how many times the
application talks to the cluster.

### 2. Summing the right collection

The advisor-book query started from `companies` in scope and `$lookup`-ed the
exposure document by document.

| | Regional (129 advisors) | Manager (16) | Advisor (1) |
|---|---|---|---|
| join per company | 13,010 ms | 2,066 ms | 495 ms |
| `advisor_id` denormalised onto `credit_exposure` | **462 ms** | **52 ms** | **16 ms** |

Only ~32% of companies have credit, and it is the exposure document that carries
the number. Copying `advisor_id` onto it turns the query into an indexed `$match`
plus a `$group`, with no per-document join. The denormalised field is derived, and
the generator is its source of truth.

### 3. Indexing the thing being compared

The concentration panel asks "how many distinct businesses hide behind these N
activity codes?" The first version embedded `cnae_descricao` on all 1.2 M
companies and ran `$vectorSearch` over that collection.

| | Documents searched | p50 |
|---|---|---|
| vector index on `companies.activity_embedding` | 1,200,000 | **29,346 ms** |
| vector index on `activities.embedding` | **32** | **181 ms** (whole panel, including the group aggregation) |

There are only 32 distinct activity descriptions in the entire base. The
comparison is between activities, not between the companies that mention them —
so the collection to index was the small one. It also removed a 512-dimensional
binary from 1.2 M documents.

This is worth telling a customer plainly, because it is the most common mistake in
vector-search projects: indexing the row instead of indexing the thing compared.

### 4. Refusing early instead of queueing everything

`tests/stress.py` mixes the five paths of the demo — mostly traversal, some
search, little semantic analysis — and ramps concurrency to 64 against the same
shared M20.

The first run passed every correctness check and failed on latency:

| Concurrency 64, before | p50 | p95 |
|---|---|---|
| whole mix | 1,038 ms | **8,798 ms** |
| ownership chain (the interactive path) | 879 ms | 2,042 ms |
| advisor book | 854 ms | 8,833 ms |
| concentration | 8,244 ms | 12,584 ms |

No 5xx, no wrong answers — just a queue in which one heavy analytical query
delayed the point lookup the screen depends on. The fix is a **bulkhead**
(`app/services/limits.py`): four concurrent slots for the book, two for the
concentration, and a **429 with `Retry-After`** for anything that cannot get a
slot within 750 ms.

| Concurrency 64, after | p50 | p95 |
|---|---|---|
| whole mix | 269 ms | **999 ms** |
| ownership chain | 205 ms | **308 ms** |
| advisor book | 934 ms | 1,422 ms |
| concentration | 438 ms | 839 ms |

Throughput went from **34 to 123 req/s**, with 615 requests refused as 429 across
the run. That is the point, not a side effect: under saturation an honest system
refuses early. Queueing everything turns a load spike into timeouts spread across
paths that still had capacity.

### Concurrency, cheap version

`tests/test_resilience.py` also runs 40 concurrent expansions: **p50 31 ms, p95
132 ms**, all 200s. That is a smoke test for contention, not a load test.

## What is not measured here

- **Sharded traversal.** `$graphLookup` requires an unsharded `from` collection
  (`LIMITATIONS.md §1`). Nothing here says anything about a sharded deployment.
- **Concurrency.** These are single-client latencies. `tests/test_resilience.py`
  runs a concurrent block, but this is not a load test.
- **Cluster sizing.** A shared M20 proves the shape of the cost, not a production
  capacity plan.
