# How this project was built

Cover page for the build briefing. The three files in `docs/briefing/` hold
architecture, modelling and interface; this file says what the project proves, what
it does not, and in what order it was built.

## What it demonstrates

That a **shallow tree queried by business key** — the shape of most enterprise
graph problems — is answered well inside the operational database, with no graph
engine alongside it and no synchronisation pipeline between two systems.

Two scenarios, one mechanism: the ownership chain behind a credit decision, and the
commercial hierarchy that decides which accounts a user may see. Around them, in
the same cluster: fuzzy entity resolution, semantic concentration analysis,
transactional review of the whole group and a change-stream alert.

## What it does not demonstrate

Behaviour at billions of edges, heavy graph algorithms running continuously
(PageRank, Louvain, centrality) and `$graphLookup` over a sharded collection. Nor
the *other* shape of graph workload — a dense network explored ad-hoc — where a
dedicated engine has a real structural advantage and the honest answer is
co-existence.

That is in `LIMITATIONS.md` (which now opens with the scope decision itself) and in
the table at the top of `COMPETITIVE.md`. Both are required reading before any
presentation — the credibility of the conversation depends on raising those limits
before the customer's architect does.

## Briefing index

| File | Contents |
|---|---|
| [`docs/briefing/01-arquitetura.md`](docs/briefing/01-arquitetura.md) | layers, invariants, order of work, how to run |
| [`docs/briefing/02-mongodb.md`](docs/briefing/02-mongodb.md) | collections, indexes, the two traversals, ACID transaction, change streams, seeds |
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
| [`tests/`](tests/) | hostile suite and mixed-workload stress |

## Order of work

1. **Data before code.** The generator (`data-generator/`) came first because the
   topology of the synthetic data is what determines whether the demo works.
   Several versions of the topology were discarded by measurement, not by opinion —
   see `docs/adr/0001-topologia-do-dado-sintetico.md`.
2. **Indexes** (`schema/indexes.js`), and the deliberate decisions about which
   indexes *not* to create.
3. **Backend** (`backend/`), with the data-access layer isolated in `app/db/`.
4. **Frontend** (`frontend/`), following the shared visual token set.
5. **Measured benchmarks** (`queries/bench.py`), filling in `benchmarks.md` and the
   tables in `LIMITATIONS.md`.
6. **Trying to break it** (`tests/test_resilience.py`), which found three bugs.
7. **Trying to overload it** (`tests/stress.py`), which found that one heavy
   analytical query was delaying the interactive path — and produced the bulkhead
   in `app/services/limits.py`.

## Corrections that changed the model

Worth knowing before reading the code, because each replaced something that looked
right and was not:

- **What counts as the top of a group.** Two failures in opposite directions: first
  discarding every legal entity that appeared as *owned*, which excluded the holding
  itself and returned a quarter of the group; then treating "no corporate owner
  among the edges walked" as the criterion, which promoted a mid-tree node to root
  whenever depth ran out. The root is now confirmed against `ownership` inside the
  same aggregation.
- **Round trips, not traversal.** The group query started as up to ten serial calls
  and 52 ms; as one aggregation it is 13 ms. On a shallow tree that is the whole
  game.
- **Indexing the row instead of the thing compared.** The activity vectors lived on
  1.2 M companies for 32 distinct texts — 29 s per concentration query. They now
  live in `activities`, one document per description.
- **A carteira that was a lottery.** Advisors were assigned per company, so a
  25-company group landed on 25 different advisors and the hierarchy meant nothing
  on screen. Portfolios now concentrate, and the showcase groups are split across
  three advisors chosen to produce a ladder: advisor 9 of 25, manager 17, regional
  25.
- **Search that ignored the screen.** Searching the whole base returned namesakes
  with no relation to the graph in front of the analyst. The scope is now applied in
  the index, and the whole-base search is an explicit entity-resolution gesture.

## State

Built and executed against a real Atlas M20 cluster. The API listens on port 8350
and the interface on 5350. Database: `graph_grupo_economico`.

**The interface is in Brazilian Portuguese** — it is presented to Brazilian banks,
and CNPJ, razão social, sócio and rating are the words those teams use. The
repository documentation is in English; code comments are in Portuguese.

Current state, measured: 67 hostile cases passing, the mixed-workload stress passing
at 64 concurrent clients, and `/health` reporting `ok` with all three search indexes
READY.
