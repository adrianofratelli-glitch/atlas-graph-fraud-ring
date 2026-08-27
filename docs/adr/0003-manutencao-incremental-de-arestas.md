# ADR 0003 — Incremental edge maintenance via the change stream

**Status:** accepted · **Date:** 2026-08-26 (revised 2026-08-27)

## Context

`materialize_connections.py` rebuilds the whole of `connections` and takes ~15
minutes at the default volume. That is acceptable for seeding the project and
unacceptable in production: the first question an architect asks about a
materialised graph is **"and when a new transaction arrives, is the graph stale
until the next batch?"**

Without an answer to that, the thesis is only half made. The argument is that the
graph lives next to the operational data — but if the graph is only updated
overnight, it is a data mart by another name, and the customer is right to be
sceptical.

## Alternatives

| Option | Freshness | Cost | Why not |
|---|---|---|---|
| Nightly batch | hours | low | that is the problem, not the solution |
| Batch every minute | ~1 min | high (scans everything) | rebuilds 118k edges to react to 3 transactions |
| Application-side trigger | immediate | medium | couples the graph write to every code path that inserts a transaction; any new producer forgets |
| **Change stream** | **~2 s** | **low** | the event already exists and is already being read |

## Decision

Maintenance runs in `backend/app/services/edge_maintenance.py`, **on the same
change stream cursor** that already fires the alerts. Opening a second stream would
double the oplog reads to consume exactly the same events.

The batch job's rules are preserved literally — if they diverge, the incremental
graph and the rebuilt graph stop being the same graph:

- only `from_account` counts (whoever **operated** the device, not whoever
  received);
- fan-out above `HUB_FANOUT_THRESHOLD` does not become an edge;
- deterministic `_id` via `uuid5` in the **same namespace** as the generator, so
  reprocessing an event rewrites the same document instead of duplicating — and an
  edge created here collides with the one the batch would create for the same pair.

The edge carries `source: "change_stream"`, which makes it possible to audit
afterwards how much of the graph came from each path.

## The revision: only react to what changes the graph

Maintenance originally reacted to **any** write on `transactions`. An embedding
backfill — one `update_many` per distinct text — fired thousands of edge
materialisations and as many SSE events, with no device or account having changed.
The customer's screen turned into a cascade of "edge created live" during an
operation that touched nothing relevant.

It now acts only on `insert`, or on an `update` whose `updatedFields` touches
`device_id`, `from_account` or `to_account`.

A related operational note worth knowing before a demo: after a large batch of
writes, the listener spends time draining the oplog from its stored `resume_token`,
and new events only appear once it catches up. Restart the backend if that
happens.

## Measured consequence

`POST /api/demo/link-accounts` inserts two transactions that make two entirely
unrelated people share a device:

| | |
|---|---|
| Edge before the insert | does not exist |
| Edge after | **~2 s**, `source: change_stream`, `weight: 2` |
| Next traversal | already walks the new edge |

Covered by `tests/test_resilience.py`, block *Edge maintenance*, which also checks
that reprocessing does not duplicate and that the listener survives.

## What this still does not solve — say it in the demo

- **Removal.** An edge that stopped making sense (the device became a hub after
  growing) only disappears at the next batch. The incremental path adds and
  updates; it does not prune retroactively.
- **Backfill.** A change stream only sees what arrives after it is up. Initial load
  and long-window recovery remain batch work.
- **Order of magnitude.** This was measured with one transaction at a time. A spike
  of thousands of transactions per second on the same device would require
  windowed grouping instead of reacting event by event.

The correct pattern is **both**: change stream for freshness, periodic batch for
consistency and pruning. That is an architecture choice, not a MongoDB limitation —
and it is the same choice you would make with any dedicated graph database on the
other side of a pipeline.
