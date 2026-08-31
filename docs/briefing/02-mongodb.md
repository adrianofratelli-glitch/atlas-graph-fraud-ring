# 02 — MongoDB

The detailed modelling, including the reasoning behind the choice between the two
graph patterns, is in [`schema/collections.md`](../../schema/collections.md). This
file covers what runs against the cluster.

## Collections

| Collection | Role | Source |
|---|---|---|
| `companies` | the credit subject, keyed by CNPJ | `generate_ownership.py` |
| `ownership` | directed ownership edge, with percentage and role | idem |
| `credit_exposure` | limit, drawn, overdue and rating per company | idem |
| `people` | individual shareholders, 800 k | `generate_people.py` |
| `advisors` | commercial hierarchy, self-referencing through `reports_to` | `generate_advisors.py` |
| `activities` | one document per distinct activity description, with its vector | `embed_activities.py` |
| `economic_groups` | ground truth of the showcase groups | `generate_ownership.py` |
| `credit_decisions` | cases opened by the ACID transaction | backend |
| `ownership_alerts` | change stream fires | backend |

Two graphs, modelled differently on purpose — an edge collection for ownership
(N:N, carries attributes), a self-referencing field for the hierarchy (functional,
carries nothing). The reasoning is in `schema/collections.md` and it is worth ten
minutes of any architecture conversation.

## Indexes

`schema/indexes.js` is the full, idempotent script. The ones without which the
traversals do not work:

| Collection | Index | Why |
|---|---|---|
| `companies` | `{cnpj: 1}` unique | the point lookup that starts every query |
| `companies` | `{advisor_id: 1}` | an advisor's book |
| `ownership` | `{owner_id: 1}` | connect field going down / follow field going up |
| `ownership` | `{owned_id: 1}` | connect field going up / follow field going down |
| `advisors` | `{reports_to: 1}` | connect field walking down the hierarchy |
| `credit_exposure` | `{company_id: 1}` unique | hydration of the group's exposure |
| `credit_exposure` | `{advisor_id: 1}` | the consolidated book, without a join per company |
| `credit_exposure` | `{review_flag: 1}` sparse | open-review queue and deterministic demo reset |

### Two deliberate decisions about indexes

**No compound index on `ownership`.** `{owned_id: 1, owner_type: 1}` would only pay
off with `restrictSearchWithMatch` filtering on `owner_type`, and the traversal does
not filter that way on purpose: cutting individual shareholders would cut exactly
the shareholder who bridges two groups the registry treats as unrelated.

**`advisor_id` denormalised onto `credit_exposure`.** It is derived data, and the
generator is its source of truth. It exists because the consolidated book was
walking `companies` in scope and looking up the exposure per document: 13 seconds
for a regional. Only ~32% of companies have credit and it is the exposure document
that carries the number, so the same answer is an indexed `$match` plus a `$group`
— 462 ms. Denormalisation with a stated owner and a stated reason, not by accident.

**`review_flag` is sparse.** Only exposures in an open review carry the field, so
indexing absent values would buy nothing. The index was added after Performance
Advisor identified the reset/queue predicate as the remaining application read
without a useful access path.

## The direction of the ownership edge

`owner_id` holds a stake in `owned_id`, and the direction **is** the information —
so the edge is not materialised both ways.

| Walking | `connectToField` | `connectFromField` |
|---|---|---|
| up: who owns me | `owned_id` | `owner_id` |
| down: whom do I own | `owner_id` | `owned_id` |

Swapping them returns the wrong set with no error at all. Both fields are indexed
for that reason.

## Building the group in one aggregation

`app/db/ownership.py` answers the whole question in a single round trip:

1. `$match` on `cnpj`, then `$graphLookup` upward;
2. candidate roots derived **in the pipeline**: legal entities with no corporate
   owner among the edges walked;
3. one indexed `$lookup` on `ownership` to confirm each candidate really has no
   corporate owner **in the database** — without it, a node in the middle of the
   tree is promoted to root whenever the depth ran out before reaching its owner,
   and depth 1 returns three quarters of the group;
4. a correlated `$lookup` that walks down from every root at once;
5. three more `$lookup` stages hydrating companies, shareholders and exposure.

The earlier version did the same work in up to ten serial calls — CNPJ lookup,
upward traversal, one downward traversal *per root in a Python loop*, three
hydration finds — and measured 52 ms against 13 ms for the same result. On a
shallow tree the traversal costs single-digit milliseconds; the response time is
decided by how often the application talks to the cluster.

## Deriving the visibility scope

`app/db/hierarchy.py` walks down `advisors.reports_to` from whoever the user is,
unions the reached ids with their own, and aggregates the book in the same
pipeline. The client sends *who the user is* and never a list of ids.

`can_see` goes the other way: from the account, to the advisor who owns it, up the
chain of command, and checks whether the user is on the path. Walking up from the
leaf is at most three hops; walking down from a manager would be their whole tree.

## Measurements

| File | What it produces |
|---|---|
| `queries/bench.py` | latency by scenario, with the network floor recorded |
| `queries/bench-results.json` | its raw output |
| `queries/load-results.json` | load throughput, written by every clean load |
| `queries/benchmarks.md` | the read version of both |

## ACID transaction

Scope: `companies.credit_status`, `companies.case_id`, `credit_exposure.case_id`
and one document in `credit_decisions`. `readConcern: snapshot`,
`writeConcern: majority`. Either the whole group goes under review or none of it
does — an intermediate state (half the group flagged, with no coherent audit
record) is worse than not having acted.

The endpoint refuses above the configured node ceiling: a group that size is
batch-job work, not a single transaction.

It also refuses to open a **second case over companies already in an open one**.
Without that, reviewing twice overwrote `case_id` and the first case became a
shell: `status: "open"` in `credit_decisions`, zero companies pointing at it.
Closing the second released everything, leaving an audit record claiming an open
review over nothing. For a project whose argument is the coherence of the record,
that state is worse than an error — and refusing is also the correct banking
behaviour.

## What the change stream does, and does not, maintain

In this project the stream reacts to the credit review changing state, to raise the
alert. It does **not** maintain a derived edge collection, because the ownership
edge *is* the source data — there is nothing to materialise.

The one derived field that does exist, `advisor_id` on `credit_exposure`, is
maintained by the generator, not by a listener. Say that out loud in an
architecture conversation: a derived field with no named owner and no refresh path
is how a demo becomes a support ticket a year later.

## Change Streams

`companies.watch()` on its own thread, with `full_document="updateLookup"`,
`max_await_time_ms` (so it does not block shutdown) and a **stored `resume_token`**,
to pick up where it left off if the cursor drops during a failover. Each SSE
subscriber has a queue capped at 200 events: a slow client must not let the
listener grow memory without a ceiling.

The `$match` runs **server-side**, over `updateDescription.updatedFields`: only a
change to `credit_status` travels the wire. A bulk load touches `companies` without
going near that field, so it produces no SSE noise at all — the filter is the
reason the listener can watch a 1.2 M-document collection safely.

The trigger is opening a credit review. That transaction marks dozens of companies
at once, and each marked document is its own change event — `update_many` over 40
companies yields 40 events, not one. The listener coalesces them by `case_id` over
a short window and publishes **one** event carrying the real count, which is also
the number the presenter wants on screen.

Two kinds of event. `review_opened` is the alert: a new case, how many companies
entered review, and the credit exposure they add up to. `review_closed` is the
counterpart — the case was closed and the companies went back to `active`. The
second exists because the A/B is what proves the listener reads state instead of
firing on its own: the same mechanism publishes both sides, and closing the review
in front of the customer returns an event in the same time window.

The event reports `stream_ms` — first event of the transaction to publish,
coalescing window included. Showing a number smaller than the one actually lived
would be demo fiction.

Worth knowing before a demo: after a large batch write the listener spends time
draining the oplog from its stored resume token, and new events only appear once it
catches up. Restart the backend after reloading the data.

## The vector index lives on the small collection

`activities` has 32 documents — one per distinct `cnae_descricao` — and the vector
index is there. It used to be on `companies.activity_embedding`, 1.2 M documents
repeating those same 32 texts, and the concentration query took **29.3 s**. Now it
is milliseconds, the index fits any tier, and the load no longer writes a
512-dimensional binary into every company.

Say it plainly in the architecture conversation: index the thing being compared,
not every row that mentions it.

## Selective filters in vector search

Scoping the concentration panel to the group on screen required no index rebuild —
the company id was already a filter field on the vector index. It did require
understanding a trap, and that trap is worth the technical conversation.

`$vectorSearch` walks the HNSW graph of the **entire collection** and applies the
filter during the walk. A very selective filter — a couple of dozen companies of one group
inside 1.2 million — discards nearly every candidate, and the search exhausts its
list before gathering results. With a low `numCandidates` the panel comes back
almost empty while the data is there.

`numCandidates: 10000` fixes it, and 10,000 is the **server ceiling**
(`"numCandidates" must be within bounds [1..10000]`), not a number someone picked —
which also bounds how selective a filter can usefully be here.

## Embeddings and pipeline order

`embed_activities.py` runs **after** the ownership base exists. Run it earlier and
the group's companies are born without their activity vector, and the concentration
panel scoped to the group comes back almost empty — with no error anywhere to
explain why.

The script is idempotent and only fills what is missing, so re-running it after any
migration that creates companies is both safe and required. `run_all.sh` has the
right order; anyone migrating existing data has to remember.

## Seeds and reproducibility

Fixed seed (`20260827`) in the generator and in `Faker`. Deterministic `_id`
everywhere. Running the pipeline again produces exactly the same dataset —
including the same showcase groups the demo script depends on.
