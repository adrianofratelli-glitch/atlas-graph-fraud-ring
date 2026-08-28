# Data modelling — `schema/collections.md`

Two graphs live in this database, and they are modelled differently on purpose.
The difference is the most useful thing in this document.

| Graph | Relationship | Modelling | Traversal |
|---|---|---|---|
| **Ownership chain** — who owns whom | N:N, carries its own attributes (percentage, role, start date) | separate edge collection `ownership` | `$graphLookup` on `ownership`, both directions |
| **Commercial hierarchy** — who reports to whom | functional: one person, at most one manager | self-referencing field `advisors.reports_to` | `$graphLookup` on `advisors`, one direction |

An edge collection for the hierarchy would only add a join: there is no attribute
to hang on the edge and no second parent to represent. A `parent_id` field on
`companies` for ownership would be worse: it cannot hold a percentage, and it
cannot express a company owned by three shareholders.

## Collections

### `companies`

The credit subject. One document per CNPJ.

| Field | Type | Note |
|---|---|---|
| `_id` | string | deterministic (`det_id`), never a random ObjectId |
| `cnpj` | string | business key, **unique index** — the main query path |
| `razao_social`, `uf`, `porte`, `situacao` | string | registry attributes |
| `cnae`, `cnae_descricao`, `setor` | string | activity; `setor` is what makes a group look diversified by code and be concentrated in fact |
| `is_holding` | bool | sparse index; picks demo cases |
| `capital_social` | double | |
| `advisor_id` | string | the advisor who covers this account — the link to the hierarchy graph |
| `credit_status`, `case_id` | string | set by the transactional review, sparse |
| `seed_index` | int | deterministic ordinal; how the generator addresses a company |

### `ownership` — the ownership edge

**Directed**: `owner_id` holds a stake in `owned_id`. The direction is the
information, so the edge is *not* materialised both ways.

| Field | Type | Note |
|---|---|---|
| `owner_id` | string | a `people` id or a `companies` id |
| `owner_type` | `individual` \| `corporate` | which collection `owner_id` points at |
| `owned_id` | string | always a `companies` id |
| `percentage` | double | |
| `qualificacao` | string | `socio-administrador`, `presidente`, … |

Going **up** the chain (who owns me) connects `owned_id` and follows `owner_id`.
Going **down** (whom do I own) connects `owner_id` and follows `owned_id`.
Swapping the two silently returns the wrong set — no error, just a different
answer. Both fields are indexed for that reason.

### `people` — individual shareholders

800,000 of them, and the number is a modelling decision, not a volume choice.
With 1.2 M companies and ~2.2 shareholders each, a population of 150,000 puts the
same person in ~17 companies: "shared shareholder" stops being an exception and
becomes a property of every pair of companies in the base. The generator draws
from a long-tailed distribution — 85% of companies draw from the wide population,
15% from a narrow 2% band that plays the recurring-shareholder role.

### `credit_exposure`

One document per company that has credit with the bank (~32% of the base).
`limite`, `utilizado`, `vencido`, `rating`. Unique index on `company_id`.

The consolidated group figure is the sum over every entity the traversal reaches —
that is the number a credit desk decides on, and it is not in any single record.

### `advisors` — the commercial hierarchy

Superintendent → regional → manager → advisor. `reports_to` points at the
superior and is `null` at the top. Indexed, because it is the `connectToField` of
the traversal that walks down the tree.

Visibility is **derived** from this tree at query time, never materialised per
user. A pre-computed list of visible accounts goes stale on every book transfer,
and between the event and the recompute somebody sees what they should not.

### `economic_groups`

Ground truth for the showcase groups: holding, members, the applicant, the
distressed sibling, the bridge shareholder and the cross-holdings. It exists so
the demo can reach a good case without scanning the base live in front of the
customer.

### `activities`

One document per **distinct** activity description — 32 of them — with the
embedding of that text. The vector index lives here, not on `companies`.

The first version embedded the field on all 1.2 M companies and indexed that: a
`$vectorSearch` over 1.2 M documents to compare 32 texts, 29.3 seconds per query.
The comparison is between activities, so the collection to index is the small one.
Getting this wrong is the most common mistake in vector-search projects — indexing
the row instead of the thing being compared — and the cost of it scales with the
table, not with the problem.

### `credit_decisions`, `ownership_alerts`

Written by the transactional review and by the change-stream listener.

## Required indexes

`schema/indexes.js` is the full script and is idempotent. The ones the traversals
cannot work without:

| Collection | Index | Why |
|---|---|---|
| `companies` | `{cnpj: 1}` unique | the point lookup that starts every query |
| `companies` | `{advisor_id: 1}` | an advisor's book |
| `ownership` | `{owner_id: 1}` | connect field going down / follow field going up |
| `ownership` | `{owned_id: 1}` | connect field going up / follow field going down |
| `advisors` | `{reports_to: 1}` | connect field walking down the hierarchy |
| `credit_exposure` | `{company_id: 1}` unique | hydration of the group's exposure |
| `credit_exposure` | `{advisor_id: 1}` | the consolidated book, with no join per company |

There is deliberately **no** compound index such as `{owned_id: 1, owner_type: 1}`.
It would only pay off with `restrictSearchWithMatch` filtering on `owner_type`,
and the traversal does not filter that way: cutting individual shareholders would
cut exactly the shareholder who bridges two groups the registry treats as
unrelated.

## Why one round trip matters more than the traversal here

The tree is shallow — most answers resolve in 2 to 4 hops, and `$graphLookup`
spends single-digit milliseconds on it. The response time is therefore dominated
by how many times the application talks to the cluster, not by graph work.

`app/db/ownership.py` assembles the whole answer in **one** aggregation: it walks
up, derives the group roots in the pipeline, walks down from all roots inside a
correlated `$lookup`, and hydrates companies, shareholders and exposure with three
more `$lookup` stages. The earlier version did the same work in up to ten serial
calls, and measured ~4× slower against the same data for the same result.

That is the shape of the argument this POV makes: for a shallow tree queried by
business key, the engine is not the bottleneck — round trips and load throughput
are.
