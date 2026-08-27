# Data modelling — `schema/collections.md`

## Collections

### `people`
```json
{
  "_id": "deterministic uuid",
  "name": "string",
  "document_id": "synthetic national id, never real",
  "phones": ["string"],
  "addresses": [{ "street": "string", "city": "string", "zip": "string" }],
  "risk_flags": ["string"],
  "ring_id": "string | null",
  "created_at": "date"
}
```

### `accounts`
```json
{
  "_id": "deterministic uuid",
  "person_id": "ref people._id",
  "account_type": "checking | credit_card | savings",
  "pix_key": "<unique per account — the DICT guarantees one key per account>",
  "pix_key_type": "cpf | evp",
  "opened_at": "date",
  "status": "active | flagged | under_investigation",
  "case_id": "string | null",
  "ring_id": "string | null"
}
```

### `devices`
```json
{
  "_id": "device_id (synthetic fingerprint)",
  "device_type": "mobile | web",
  "first_seen": "date"
}
```

### `transactions`
```json
{
  "_id": "deterministic uuid",
  "from_account": "ref accounts._id",
  "to_account": "ref accounts._id",
  "to_pix_key": "destination PIX key — what same_pix_counterparty groups on",
  "device_id": "ref devices._id",
  "amount": "number",
  "reason_text": "string (free text — used in the vector search demo)",
  "reason_embedding": "BinData float32 (Voyage embedding)",
  "timestamp": "date"
}
```

### `connections` (explicit edge pattern)
```json
{
  "_id": "deterministic uuid",
  "from": "ref people._id",
  "to": "ref people._id",
  "type": "shares_device | shares_address | same_pix_counterparty",
  "weight": "number (size of the group that produced the edge)",
  "shared_key": "the attribute value that produced it",
  "source": "batch | change_stream",
  "created_at": "date"
}
```

## Why the PIX edge is a destination, not a shared key

The obvious modelling is wrong, and it is worth stating plainly because it is the
first thing a bank architect will check.

Brazil's DICT directory (BCB Resolution No. 1/2020) guarantees that **one PIX key
addresses exactly one transactional account at a time**, and rejects duplicate
registration. Two accounts holding the same key is not a rare state — it is a state
the payment system prevents. A `shares_pix_key` edge would describe something that
cannot happen.

The real signal is the other side of the payment: **separate accounts paying
repeatedly into the same destination key**. That is the collection account at the
end of a mule funnel, and it is the pattern anti-money-laundering teams actually
hunt. So `pix_key` lives on `accounts` with a `unique` index, `transactions`
carries `to_pix_key`, and the edge is built by grouping on it.

Two guards keep legitimate behaviour out: a payer must have paid the same key more
than once (a single payment to a merchant is a purchase, not complicity), and a key
receiving from more people than `HUB_FANOUT_THRESHOLD` is a hub — a merchant, a
payment provider, a utility — and does not become an edge.

## Two graph modelling patterns — when to use each

### Pattern A — shared attribute as an implicit edge

There is no `connections` collection for this kind of relationship. The "edge" is
inferred at query time: two accounts that share the same `device_id` are implicitly
connected.

**When to use it:** when the relationship is already a natural attribute of the
operational data (device, address, phone) and there is no business reason to keep
it as an entity of its own. Advantage: zero duplication, zero synchronisation
maintenance. Disadvantage: `$graphLookup` has to look up `devices`/`addresses` at
every hop, which is more expensive than following an already materialised edge.

Example query (see `queries/02_graphlookup_shared_attributes.js` for the full
version):
```javascript
db.accounts.aggregate([
  { $match: { _id: "suspect-account-id" } },
  {
    $graphLookup: {
      from: "transactions",
      startWith: "$_id",
      connectFromField: "device_id",
      connectToField: "device_id",
      as: "network",
      maxDepth: 4,
      depthField: "hops"
    }
  }
])
```

### Pattern B — explicit edges in `connections`

The relationship is materialised as its own document, with weight and type. It is
the pattern closest to a traditional graph database.

**When to use it:** when the relationship itself carries relevant business metadata
(weight, type, creation date, who declared it), or when traversing via an implicit
attribute would be too expensive at high fan-out.

```javascript
db.people.aggregate([
  { $match: { _id: "suspect-person-id" } },
  {
    $graphLookup: {
      from: "connections",
      startWith: "$_id",
      connectFromField: "to",
      connectToField: "from",
      as: "network",
      maxDepth: 4,
      depthField: "hops",
      restrictSearchWithMatch: { weight: { $lte: 50 } }
    }
  }
])
```

**Recommendation:** use Pattern B (`connections`) for the core of the investigation
(cheaper and more controllable via `restrictSearchWithMatch`), and Pattern A only
as the source that *populates* `connections` — a job that materialises shared
attributes as edges, with a minimum strength threshold, so that generic hubs do not
become investigation edges.

## Required indexes (see `schema/indexes.js` for the full script)

| Collection | Index | Reason |
|---|---|---|
| `connections` | `{ from: 1 }` | `connectFromField` of `$graphLookup` |
| `connections` | `{ to: 1 }` | `connectToField` of `$graphLookup` |
| `connections` | `{ type: 1, from: 1 }` | filter by edge type |
| `connections` | `{ from: 1, weight: 1 }` | pruning filter resolved in the index, not in FETCH |
| `transactions` | `{ device_id: 1 }` | Pattern A traversal |
| `transactions` | `{ from_account: 1 }`, `{ to_account: 1 }` | operational queries + graph entry |
| `transactions` | `{ to_pix_key: 1 }` | materialisation of `same_pix_counterparty` |
| `transactions` | `{ simulated: 1 }` | demo reset without a collection scan |
| `transactions` | `{ reason_text: 1, reason_embedding: 1 }` | embedding backfill without a collection scan |
| `accounts` | `{ pix_key: 1 }` unique | one key per account, as the DICT requires |
| `accounts` | `{ ring_id: 1 }` | ground-truth validation in the demo |
| `people` | Atlas Search index (`autocomplete` on `name`, `token` on `ring_id`) | fuzzy entity resolution, scoped by ring |
| `transactions` | Vector Search index on `reason_embedding`, filter on `ring_id` | semantic similarity, scoped by ring |

## Who creates the `connections` indexes

Not `schema/indexes.js`, and the reason matters: `materialize_connections.py
--rebuild` calls `drop()` on the collection, and `drop()` takes the indexes with
it. Creating the indexes in an earlier pipeline step means losing them silently —
the traversal keeps working, just by collection scan at every BFS level.

That actually happened in this project and went unnoticed for a while, because
nothing breaks. The indexes are now created at the end of the materialisation
itself, and `run_all.sh` verifies they exist before declaring success.

## Incremental maintenance

The batch job is not the only write path into `connections`. The change stream in
`backend/app/services/edge_maintenance.py` materialises the edge as soon as the
transaction arrives, in about two seconds, preserving the same rules: `from_account`
only, a hub above the threshold does not become an edge, and a deterministic `_id`
in the same namespace as the generator — so an edge created live collides with the
one the batch would create for the same pair, instead of duplicating it.

Edges from that source carry `source: "change_stream"`, which makes it possible to
audit afterwards how much of the graph came from each path.

Maintenance only reacts to `insert`, or to an `update` whose `updatedFields`
touches `device_id`, `from_account` or `to_account`. Without that guard, any write
on `transactions` reopens the materialisation — an embedding backfill fired
thousands of edge writes with no device or account having changed.

## Resilience of the model

- Every `_id` is deterministic (a hash of key attributes), not a random
  `ObjectId` — that is what makes the data generator idempotent (running it twice
  does not duplicate).
- `ring_id` exists on `people`, `accounts` and `transactions` — it serves as
  traceable *ground truth* to validate that the demo always finds the expected
  network, regardless of generator randomness.
- `accounts.pix_key` is unique. If someone tries to give two accounts the same key,
  the write fails instead of producing an edge that does not exist in real life.
