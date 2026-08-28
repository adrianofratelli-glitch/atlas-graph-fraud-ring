# ADR 0001 — Topology of the synthetic data

**Status:** accepted · **Date:** 2026-08-26 (rewritten 2026-08-28 when the project's
use case moved from dense fraud rings to ownership chains)

## Context

The demo depends on the group growing visibly between depth 1 and depth 2, and on
staying stable after that. That is not a property of `$graphLookup`: it is a
property of the data. A generated ownership base can easily produce either a bare
triangle or the whole group at the first hop, and both kill the demonstration.

## Alternatives measured

| Topology | Measured result | Why it was discarded |
|---|---|---|
| Holding with a single level of subsidiaries (the first version) | depth 1 and depth 6 returned exactly the same companies | the depth control decides nothing; the presenter clicks and nothing changes |
| Groups of 11 companies over four levels | the reveal worked, but the graph on screen read as a thin tree | for a conglomerate story, 11 nodes does not look like a conglomerate |
| Uniform shareholder assignment across 150,000 individuals | ~17 companies per person | every pair of companies shares a shareholder; the finding stops being a finding |

## Decision

**Four levels, 25 companies, one sector per group.** Holding → 3 sub-holdings → 9
controlled → 12 operating, with the credit applicant at the bottom so that walking
up matters.

**Cross-holdings.** Two edges per showcase group deliberately break the tree, so a
node is reachable by two paths. Without them the traversal never revisits an edge
and the deduplication of the result is untested — which is how a real group behaves
and a generated one usually does not.

**A bridge shareholder every two groups.** One individual holds a minority stake in
two conglomerates the registry treats as unrelated. It is the finding that does not
exist in any single record. One in two, not all — if every group had one, it would
not be news.

**One sector per group, many CNAE codes.** Each showcase group draws its activity
codes from a single sector, so the group looks diversified by code and is
concentrated in fact. That is what the vector-search panel finds on its own, and it
is real credit concentration.

**A long-tailed shareholder distribution over 800,000 individuals.** 85% of
companies draw from the wide population; 15% draw from a narrow 2% band that plays
the recurring-shareholder role. The population size is a modelling decision, not a
volume one.

## The revision that mattered

The first version defined a group root as "a legal entity with no corporate owner
**among the edges already walked**". Two failures came out of that, in opposite
directions:

1. discarding any entity that appeared as *owned* excluded the holding itself — its
   individual shareholders produce edges where the holding is the owned side — and
   the group came back with two companies instead of eight, with no error;
2. after fixing that, a node in the middle of the tree was promoted to root
   whenever the depth ran out before reaching its owner. At depth 1 the descent
   from that false root returned 19 of the group's 25 companies, and the depth
   control looked broken.

The root is now confirmed against the database with one indexed `$lookup` inside
the same aggregation: a root is a company with no corporate owner **in
`ownership`**, not one that happens to be missing from the walked set.

## Measured consequence

Entering through the applicant of a showcase group:

| Depth | Companies | Consolidated limit | Overdue |
|---|---|---|---|
| 1 | 5 | R$ 11.8 M | R$ 0 |
| 2 | 25 | R$ 197.2 M | R$ 11.2 M |
| 3+ | 25 | R$ 197.2 M | R$ 11.2 M |

The applicant's own record: rating A, R$ 2.1 M limit, nothing overdue. The reveal
between depth 1 and depth 2 is the demo, and the flat line after it is the
argument — a shallow tree saturates, so depth costs nothing here.
