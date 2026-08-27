# How this project was built

Cover page for the build briefing. The three files in `docs/briefing/` hold
architecture, modelling and interface; this file says what the project proves, what
it does not, and in what order it was built.

## What it demonstrates

That multi-hop traversal, fuzzy entity resolution, semantic similarity,
transactional flagging and real-time alerting all fit **on the same platform that
already holds the operational data** — with no graph database alongside it and no
synchronisation pipeline between two systems.

## What it does not demonstrate

Behaviour at billions of edges, heavy graph algorithms running continuously
(PageRank, Louvain, centrality) and `$graphLookup` over a sharded collection. That
is in `LIMITATIONS.md`, and it is required reading before any presentation — the
credibility of the conversation depends on raising those limitations before the
customer's architect does.

## Briefing index

| File | Contents |
|---|---|
| [`docs/briefing/01-arquitetura.md`](docs/briefing/01-arquitetura.md) | layers, invariants, order of work, how to run |
| [`docs/briefing/02-mongodb.md`](docs/briefing/02-mongodb.md) | collections, indexes, pipelines, edge materialisation, seeds |
| [`docs/briefing/03-interface-fluxos.md`](docs/briefing/03-interface-fluxos.md) | frontend, screens, streaming, demo path |

Positioning documents, outside the build briefing:

| File | Contents |
|---|---|
| [`README.md`](README.md) | public cover of the repository |
| [`LIMITATIONS.md`](LIMITATIONS.md) | where the thesis **does not** apply |
| [`COMPETITIVE.md`](COMPETITIVE.md) | MongoDB × Neo4j × Neptune, and the qualifying questions |
| [`queries/benchmarks.md`](queries/benchmarks.md) | numbers measured on this cluster, not estimated |
| [`docs/demo-script.md`](docs/demo-script.md) | 15-minute script and pre-demo checklist |
| [`docs/business-case.md`](docs/business-case.md) | turning the demo into a number, and what we refuse to estimate |
| [`docs/adr/`](docs/adr/) | recorded architecture decisions |
| [`tests/`](tests/) | hostile suite, scale test and index tuning |

## Order of work

1. **Data before code.** The generator (`data-generator/`) came first because the
   topology of the synthetic data is what determines whether the demo works. Three
   versions of the topology were discarded by measurement, not by opinion — see
   `docs/adr/0001-topologia-do-dado-sintetico.md`.
2. **Indexes and edge materialisation** (`schema/`, `materialize_connections.py`).
3. **Queries in `mongosh`** (`queries/`), readable without starting anything.
4. **Backend** (`backend/`), with the data-access layer isolated in `app/db/`.
5. **Frontend** (`frontend/`), following the shared visual token set.
6. **Measured benchmarks** (`queries/bench.py`), filling in `benchmarks.md` and the
   tables in `LIMITATIONS.md`.
7. **Trying to break it** (`tests/test_resilience.py`), which found three bugs.
8. **Scale measurement** (`tests/scale_graph.py`), which revealed the 100 MB
   `$graphLookup` ceiling and showed that pruning is what makes deep traversal
   possible.

## Corrections that changed the model

Two of them are worth knowing before reading the code, because both replaced
something that looked right and was not:

- **The PIX key edge.** The first model linked two people by the *same* key. Brazil's
  DICT directory guarantees one key per transactional account, so that state cannot
  exist. The edge is now the shared **destination** key — the collection account at
  the end of the mule funnel. See `docs/briefing/02-mongodb.md`.
- **The funnel topology.** Spreading the funnel across fixed collectors turned each
  key's payers into a clique and collapsed the ring's diameter; entering from any
  node brought 22 of 30 members at the first hop. Paying the branch parent makes
  the payers siblings, and the reveal by depth (7 → 16 → 31) survives.

## State

Built and executed against a real Atlas M20 cluster. The API listens on port 8350
and the interface on 5350. Public-facing documentation and the interface are in
English; code comments are in Portuguese.
