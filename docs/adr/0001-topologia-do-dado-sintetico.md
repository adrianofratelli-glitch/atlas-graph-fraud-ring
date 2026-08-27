# ADR 0001 — Topology of the synthetic data

**Status:** accepted · **Date:** 2026-08-26 (revised 2026-08-27)

## Context

The demo depends on the graph growing visibly between depth 1 and depth 4. That is
not a property of `$graphLookup`: it is a property of the data. Three topologies
were tested and measured before the backend existed.

## Alternatives measured

| Topology | Measured result | Why it was discarded |
|---|---|---|
| One address for the whole network | a 20-member ring complete at depth 1 (422 edges, 20 nodes) | it becomes a clique; nothing is left for step 3 of the script to show |
| Chain (each member linked to the next) | would need depth 19 to reveal a 20-member ring | above the backend cap, and the growth is too linear to read |
| Shared attribute drawn at random in the clean population | average degree 8.6; depth 2 reached 1,096 of 2,000 nodes | the fraud ring is lost in the noise; the graph becomes unreadable |

## Decision

A tree with branching factor **4**, with the shared attributes following the same
tree and never the whole ring:

- **device:** each parent/child pair shares a device — it is the edge that creates
  the tree;
- **address:** shared within a branch (parent + direct children);
- **destination PIX key:** each member pays into the account of their branch's
  parent, so the payers of one key are exactly the **siblings** of that branch.

In the clean population, sharing is **structured, not drawn at random**: households
of 2 to 3 people (18% of the population) and 4% of transactions operated from a
neighbouring account's device. Random people sharing an address would create an
artificial connected component.

## The revision that mattered

The first version of the PIX funnel spread payments across five fixed collectors
per ring. Materialisation turns the payers of one key into a clique, and cliques of
six to seven members collapsed the ring's diameter: entering from **any** node
brought 22 of 30 members at the first hop. The reveal by depth — the spine of the
demonstration — stopped existing.

Paying the branch parent fixes it: groups of four, and the PIX edge links sibling
to sibling, complementing the device edge (parent to child) instead of duplicating
it.

A second, related lesson came from choosing the entry point. Ranking ring members
by degree picks the funnel collector, and entering there also brings the whole ring
at depth 1. Ranking by *low* degree picks a leaf, and the ring only closes at depth
5. The leader — the root of the tree — is the node that gives the readable curve,
and that is what `_entry_nodes` prefers; the degree ranking is only a fallback for
rings whose leader has no materialised edges.

## Measured consequence

A 30-member ring, entering through the leader, on the full dataset (150,000
people):

| Depth | Nodes | Ring members reached |
|---|---|---|
| 1 | 7 | 7/30 |
| 2 | 16 | 16/30 |
| 3 | 31 | **30/30** |
| 4 | 31 | 30/30 |
| 5 | 31 | 30/30 |

The reveal is gradual and readable — which is what step 3 of the script needs. From
depth 3 on the traversal saturates: the ring is a closed component, and there is no
leakage into the clean population.

The edge-type toggle also became meaningful. At depth 3, disabling the device edge
collapses the network from 31 nodes to 3, while disabling address or destination
key changes almost nothing — so the analyst can test, live, which link actually
holds the ring together.

**This is a consequence, not a goal, and it changed the script.** An earlier version
of this ADR predicted that depth 4 would start reaching legitimate people, which was
confirmed on the reduced test dataset (where hubs fall below the pruning threshold)
but **not** on the full dataset, where they are discarded at materialisation.
Degradation by fan-out exists and is measurable — it just is not here. It is
measured in `LIMITATIONS.md §4`, starting from a hub, and the script was corrected
to show it there.

Documenting the prediction that did not hold is deliberate: it is exactly the kind
of detail an architect on the customer's side will test.
