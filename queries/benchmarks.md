# Benchmarks

> Numbers measured by `queries/bench.py` against the real cluster. Never replaced
> by an estimate or a market number — the value of this document is that the
> customer can reproduce it with one command.
>
> ```bash
> .venv/bin/python queries/bench.py --runs 20
> ```
>
> The first run of each scenario is discarded: it pays for WiredTiger cache
> warm-up and would measure the wrong thing.

## Test environment

| | |
|---|---|
| Cluster tier | M20 (4 GB RAM, 150 GB disk), shared with other applications |
| Network latency to the cluster | **8.5 ms** average (p95 10.4 ms) |
| Volume | 150,000 people · 239,912 accounts · 240,939 devices · 604,285 transactions · 118,720 connections |
| Injected rings | 40 (20 to 30 members each) |
| Timing measured | 2026-08-26 · node counts refreshed 2026-08-27 |

**Read the network latency before any other number.** Every time below already
includes the ~8.5 ms network floor; what the benchmark measures is the
**increment** over that floor, which is the part attributable to `$graphLookup`.

> Two earlier rounds were measured across slower network paths, with floors of
> **256 ms** and **324 ms**. The floor dominated everything: a depth-3 expansion
> showed up as "275 ms" when the real work was about 1 ms. Two conclusions from
> those rounds were network artefacts and have been corrected — the main one being
> that pruning made the query *slower*, which was noise. Worth keeping as a
> reminder: measuring from the wrong side of the network measures the network.
>
> If you re-run `bench.py` from a high-latency path, expect every row to shift by
> the floor. Compare increments, not absolutes, and record the ping.

## Index tuning for the pruned path

`restrictSearchWithMatch: {weight: {$lte: N}}` is the UI default. With `{from: 1}`
alone, that filter runs at the **FETCH** stage: the server fetches the document and
only then discards it. Measured on a degree-400 node in the scale graph:

| Index | Returned | Keys examined | Docs examined |
|---|---|---|---|
| `{from: 1}` | 227 | 400 | **400** |
| `{from: 1, weight: 1}` | 227 | 227 | **227** |

Effect on time, same node:

| | `{from: 1}` | `{from: 1, weight: 1}` | |
|---|---|---|---|
| depth 1 | 81.0 ms | **58.9 ms** | **−27%** |
| depth 2 | 3,311 ms | 3,461 ms | +5% (noise) |

**Honest reading:** the compound index helps the shallow, pruned traversal, which is
what the screen does. At greater depth the cost is assembling the result, not
fetching keys, and the index changes nothing. Cost: 26.4 MB on a 2.4 M edge
collection, about 1%. Worth it, and it is not the difference between viable and
unviable — that is pruning.

Reproduce with: `.venv/bin/python tests/index_tuning.py`

## A note on the first version of these numbers

Benchmarks published before 2026-08-26 were measured with `connections`
**missing its traversal indexes**. `materialize_connections.py --rebuild` calls
`drop()`, which takes the indexes with it, and the pipeline created them in an
earlier step — so the BFS ran a COLLSCAN of 117,974 documents at every level.
Nothing broke; it was just slow.

Effect of the fix at depth 3: **525 ms → 275 ms**. The indexes are now created by
the materialisation itself, `run_all.sh` verifies they exist, and
`tests/test_resilience.py` fails if the plan goes back to COLLSCAN.

It is recorded here because a benchmark number that halved deserves an explanation,
not a silent replacement.

## `$graphLookup` — Pattern B (explicit edges in `connections`)

| Depth | Mean (ms) | p95 (ms) | Nodes returned |
|---|---|---|---|
| 1 | 9.2 | 11.0 | 7 |
| 2 | 9.1 | 10.0 | 16 |
| 3 | 9.5 | 10.4 | 31 |
| 4 | 10.0 | 12.1 | 31 |
| 5 | 11.0 | 13.1 | 31 |

Times are from the 8.5 ms floor run; node counts are from the current topology,
where the destination-PIX-key edge links siblings within a branch.

## `$graphLookup` — Pattern A (implicit attribute via `device_id`)

| Depth | Mean (ms) | p95 (ms) | Accounts reached |
|---|---|---|---|
| 1 | 9.4 | 11.7 | 2 |
| 2 | 11.9 | 42.1 | 2 |
| 3 | 10.9 | 29.2 | 2 |

Pattern A returns **transactions**, not people. Turning those into person nodes
would require the `$lookup`s Pattern B already paid for once, in the materialisation
job. Comparing the two times directly is unfair to Pattern B: it delivers more.

## Which edge type holds the ring together

Same entry point, same depth 3, toggling edge types in the UI:

| Edges enabled | Nodes reached |
|---|---|
| all three | 31 |
| without destination PIX key | 31 |
| without address | 30 |
| **without device** | **3** |

Not a performance number — an investigative one. The device fingerprint is what
holds this ring together; the other two are reinforcement. It is a hypothesis the
analyst tests live, without writing a query.

## Impact of `restrictSearchWithMatch` (hub pruning)

| Scenario (depth 4) | Mean (ms) | Nodes returned |
|---|---|---|
| without `restrictSearchWithMatch` | 11.9 | 31 |
| with weight pruning (`weight <= 50`) | 10.3 | 31 |

## ACID transaction — flagging the network for investigation

Measured through `POST /api/investigation/flag`, which is the same path the demo
uses:

| Network size | Accounts updated | Time (ms) |
|---|---|---|
| 30 people | 44 | 42 warm, ~1,050 cold |

`readConcern: snapshot`, `writeConcern: majority`, three writes in the same commit
(`accounts`, `people`, `investigations`).

## Change Streams — alert latency

Measured by the listener itself (`lookup_ms` in the alert payload) and visible in
the UI on every fire. The network check — the `find` on `accounts` that decides
whether the transaction touches an open case — runs in the tens of milliseconds;
the dominant time between insert and alert on screen is oplog propagation plus
network RTT.

## Scale — traversal over 2.4 million edges

> Reproduce with: `.venv/bin/python tests/scale_graph.py --build --measure`
>
> The injected fraud rings have 30 members and form closed components. They prove
> the traversal **finds** the network; they prove nothing about cost, because the
> BFS never does heavy work. This section answers "and at my volume?" with a
> number.

**Test graph:** preferential attachment over the same 150,000 people, **2,398,814
directed edges**, long-tailed degree distribution capped at 400. Collection
`connections_scale`, indexed on `from` and `to`.

| Entry | Depth | Nodes reached | Edges | Time |
|---|---|---|---|---|
| degree 340 | 1 | 10,668 | 11,894 | **110 ms** |
| degree 340 | 2 | — | — | ✗ **exceeds 100 MB after 38 s** |
| degree 14 | 1 | 626 | 642 | 12.6 ms |
| degree 14 | 2 | 21,278 | 25,809 | **254 ms** |
| degree 14 | 3 | — | — | ✗ exceeds 100 MB after 180 s |
| degree 12 | 1 | 363 | 376 | 11.8 ms |
| degree 12 | 2 | 13,597 | 15,823 | **153 ms** |
| degree 12 | 3 | — | — | ✗ exceeds 100 MB after 76 s |

**Two hops are interactive even reaching 21,000 nodes** — 254 ms. Three hops do not
exist without pruning: the traversal reaches most of the component and blows past
the 100 MB output-document ceiling (`LIMITATIONS.md §5`), after grinding for 38 to
180 seconds.

### Pruning is what makes the query possible

Same high-degree entry, same depth 1, with and without `restrictSearchWithMatch`:

| | Nodes | Time |
|---|---|---|
| without pruning | 10,668 | 104 ms |
| **with pruning** (`weight <= 5`) | 3,620 | **45 ms** |

And in an earlier measurement, with a degree-18 node at depth 3: **without pruning
the traversal exceeded 100 MB after 40.7 s; with pruning, 33,872 nodes in
1,044 ms.** From impossible to one second.

`restrictSearchWithMatch` prunes **during** the traversal, before the output array
grows. A `$match` after `$graphLookup` would save nothing — the stage would already
have blown up.

### What these numbers say, and what they do not

**They say:** on a 2.4 M edge graph on a shared M20, traversal of 1 to 2 hops is
interactive (12–254 ms) for any degree profile, and depth 3 requires pruning to
exist at all. Fraud investigation is a 1 to 3 hop case, so this covers the use
case.

**They say nothing** about billions of edges, a sharded graph (`LIMITATIONS.md §1`)
or continuous algorithms (`§3`). And the 100 MB ceiling (`§5`) is structural: it
does not go away with a larger tier.

**What would change:** more RAM reduces the time of the cases that fit, but does not
lift the ceiling. For components larger than that, the answer is to pre-compute
subgraphs, not to buy a bigger machine.

## Vector search with a selective filter

Scoping the semantic panel to one ring uses `ring_id`, a filter field on the vector
index. `$vectorSearch` walks the HNSW graph of the whole collection and applies the
filter during the walk, so a very selective filter starves the search:

| `numCandidates` | Distinct reasons returned from a 137-transaction ring |
|---|---|
| 360 | **1** |
| 10,000 (server ceiling) | **8** |

The data held nine distinct reasons. This is a design limit, not a tuning knob:
`"numCandidates" must be within bounds [1..10000]`.

## Honest conclusions

**On the demo network, the traversal is essentially free.** Network floor 8.5 ms;
depth 1 costs 9.2 ms and depth 5 costs 11.0 ms. The work attributable to
`$graphLookup` runs from ~0.7 ms to ~2.5 ms. The curve is flat because the network
saturates at the ring's members by depth 3.

That proves the traversal finds the network at no perceptible cost, and **proves
nothing about volume** — for that, see *Scale*.

**Pruning helps, and an earlier measurement said otherwise because of the network.**
With a 256 ms floor, "273 ms without pruning against 284 ms with" was noise read as
signal. With an 8.5 ms floor: 11.9 ms without against 10.3 ms with, at depth 4. And
on the scale graph the difference is of another order: 104 ms against 45 ms, or
"impossible" against "one second" at depth 3.

**The pruning that matters most is still the one at materialisation.**
`materialize_connections.py --report-only` shows what it discards:

| Hub type | Groups discarded | Reach |
|---|---|---|
| address | 3 | 8,618 people |
| device | 5 | 35,251 accounts |

Without that discard, those 8,618 people would be mutually connected one hop apart.

**Pattern A and Pattern B are not comparable by the clock.** Both land around 10 ms
because both are trivial at this scale; Pattern A reaches 2 accounts and Pattern B
reaches 31 person nodes. The reason to prefer Pattern B for investigation is not
speed: it is `restrictSearchWithMatch`, which only exists once the edge is
materialised.

**What can still be optimised:** the compound index `{from: 1, weight: 1}` (section
above, −27% on the pruned path) is already applied. Beyond that, what remains is
architecture, not tuning: on-demand expansion instead of the whole neighbourhood,
and pre-computed subgraphs for components that do not fit under the 100 MB ceiling.
