# Presentation script

## Before you start

1. `curl -s localhost:8350/health | jq` returns `"status": "ok"`. It validates, in
   one shot: the Atlas connection, the collection counts, the status of the
   Search/Vector indexes and the latency of a reference `$graphLookup`.
2. If `status` comes back `degraded`, the `checks` field says exactly what. An
   index in `BUILDING` is not a blocker: the traversal works, just cut steps 6
   and 7.
3. `POST /api/demo/reset` to make sure nothing is left flagged from a previous
   session. It also removes edges created live in step 10, so the "before" of that
   step is true again.
4. Open the UI and confirm that an account from the **clean population** renders a
   small graph. Starting the demo with the result already on screen kills step 2.
5. Note a `ring_id` from the selector. If something fails live, `mongosh` with
   `queries/01_graphlookup_explicit_edges.js` shows the same numbers.
6. Test steps 8 and 9 once beforehand: flag the network, inject the transaction,
   see the alert. Then `reset`.
7. The first call to `/api/entry-points` takes about a second and the rest are
   instant, because the result is cached. Opening the screen once beforehand takes
   that second off the critical path.
8. If you have just run a large batch write against `transactions` (a data
   regeneration, an embedding backfill), **restart the backend**. The change stream
   listener resumes from its stored token and spends time draining the oplog; new
   events only appear once it catches up.

## Script (~15 minutes)

### 1. Opening — the problem (2 min)
Select an account from the clean population. Small graph, nothing red. Ask: *"with
the data you have today, what would you say about this account?"*

### 2. The question that changes everything (1 min)
*"And what if this account operated from the same device, at the same address, and
paid into the same PIX key as twenty others that also look normal in isolation?"*
Switch to the account under suspicion, depth 1. It still looks like very little.

Two points of wording, because the audience is a bank:

- say **device fingerprint**, not "they share a phone". "Sharing a phone" sounds
  like a married couple and weakens the case; a repeated fingerprint across
  unrelated national IDs is a mule farm, which is what anti-fraud teams hunt.
- the PIX key is the **destination** key. Two accounts holding the same key does
  not exist: DICT guarantees one key per account. If you say "the same PIX key",
  the customer's architect takes the demo apart — and they would be right. The link
  is that they pay into the same key.

Click a node in the ring to **pin** it: the panel stops following the mouse and you
can read its accounts, how many there are and when they were opened. Accounts under
different national IDs opened in the same window, on the same device, is evidence
that needs no narration.

### 3. Run the traversal live (3 min)
Depth 2, then 3. Narrate what is happening: **one** aggregation query, without
leaving MongoDB, without synchronising to another system. The time on screen comes
from the execution, not from `benchmarks.md`.

### 4. The complete network (2 min)
At depth 3 the injected network closes. Red nodes, thick border. Point at the "in
ring" metric — that is ground truth matching.

Then point at **At risk**. That tile is the volume those accounts actually moved,
summed from `transactions` on the live cluster — not a projection. It is the
answer to "why should I care about these thirty accounts?", and it is the first
number in the demo that a non-technical person in the room can act on.

### 5. Which link actually holds the ring together (2 min)
This is where the checkboxes earn their place. Still at depth 3, uncheck one type
at a time:

| Edges enabled | Nodes reached |
|---|---|
| all three | 31 |
| without destination PIX key | 31 |
| without address | 30 |
| **without device** | **3** |

The device fingerprint is what holds this ring together. Address and destination
key are reinforcement. That is a hypothesis being tested live, in front of the
customer, without anyone writing a query — and it is the moment the graph stops
being a picture and becomes an instrument.

### 6. Where it starts to hurt, and you are the one raising it (2 min)
Go up to 4, 5 and 6. **The graph does not grow**: it saturates at the ring's
members. Say that out loud, because it is the opposite of what people expect to
hear in a demo. The ring is a closed component, and extra depth reaches nobody
else.

Then show where the cost actually lives. Keep the numbers from
`queries/benchmarks.md`, section Scale, to hand. On a graph of 2.4 million edges:

- two hops reach 21,000 nodes in 254 ms;
- three hops, without pruning, **do not finish**: they exceed the 100 MB limit
  `$graphLookup` has for assembling its result, after grinding for 40 to 180
  seconds;
- the same three hops, with pruning, finish in **1 second**.

That pair is the entire argument. Pruning is not a convenience filter, it is what
makes the query possible — and it works because it happens during the traversal,
before the result grows. A filter after the stage would save nothing.

Toggle **Prune hubs** on and off while you talk. On the small demo ring the
difference is milliseconds, because materialisation already discarded the hubs:
`materialize_connections.py --report-only` shows the 3 addresses (8,618 people) and
5 devices (35,251 accounts) that were left out. Say that too. Showing where the
optimisation makes **no** difference is what earns credit for the case where it
does.

### 7. Fuzzy entity resolution (2 min)
The Atlas Search panel already shows which name in the dataset is misspelled.
Search for it: the real ring record appears at the top. A `$graphLookup` on exact
equality would never make that link — and it took no second search engine.

The panel has two scopes, and the difference between them is a good question for
the audience. Type a common first name, **"Diego"**:

- under **Whole database**, the Diego who is in the network comes first tagged
  `IN NETWORK`, and below him come the Diegos who have nothing to do with the case,
  tagged `OUTSIDE`;
- under **This network only**, one is left.

Say why the default is the whole database: **the case that gives this panel its
value is precisely a record the graph cannot reach.** The misspelled twin has no
edge linking her to the network. Always scoping would fix the noise and kill the
demonstration with it; annotating every result fixes both.

### 8. Semantic similarity (2 min)
Leave the scope on **This network only** and hit Search. The query is a paraphrase
that does not exist in the corpus, and the useful answer is not a phrase — it is
the number at the top: **eight different ways of saying the same thing inside that
network**, with an average ticket in the thousands.

That is the investigative reading: thirty accounts that do not know each other
justify transfers of R$ 3,000 with eight variations of the same excuse. Keyword
search does not group that, because the phrases share no words.

Switch to **Whole database** and compare the ticket: the same "consultancy" reasons
in the legitimate population are worth R$ 280, not R$ 3,400. The contrast appears
on its own.

If anyone asks about cost, this step has a measured trap worth telling: a selective
filter in vector search needs a high `numCandidates`, because the ANN walks the
graph of the entire collection and discards during the walk. With 360 candidates
the network returned **one** reason; the data held nine. The server ceiling is
10,000, and that is a design limit, not a tuning knob.

### 9. Action — flag the network (2 min)
Be at **depth 3** before clicking: at hop 1 the button flags 7 nodes, and blocking
part of a ring is not the action an analyst would take. The screen itself warns you
when you are shallow.

**Flag N nodes for investigation.** An ACID transaction updates accounts, people and
the audit record together. The case card opens in the same column, with
`readConcern: snapshot`, `writeConcern: majority`, the commit time and the
before/after of the accounts (`active` → blocked). **On the graph, flagged nodes get
a dashed border** — the action becomes visible where the investigation happens.

Explain why an intermediate state — half the network blocked, incoherent audit — is
worse than not having acted.

If the audience owns a budget, open **"What this case is worth"**. It splits into
two blocks, and the split is the point:

- **Measured** — the volume, operations, accounts and window, straight from
  `transactions`;
- **Your number** — hours per case and cost per hour, which come from `.env` and
  are the bank's own figures, not ours. The panel says so on screen.

The arithmetic is one line: today a case is opened per account, here one case
covers the ring the traversal found. Say plainly that we do **not** estimate loss
avoided — converting exposure into realised loss needs a rate that varies by
product and institution, and guessing it would undo the credibility of every
measured number in the demo. That refusal lands better than a big invented figure.

Full framing, plus the discovery questions that fill the two inputs, is in
[`docs/business-case.md`](business-case.md).

If the audience is a compliance team, open **"What the bank is now required to do"**
inside the card. It shows three obligations that come entirely from the audit record
that committed alongside the block, with no integration to any system:

- **COAF** (BCB Circular 3.978/2020): report a suspicious operation within 24 hours
  of the decision — and the screen shows the clock running from the case's
  `opened_at`.
- **MED** (BCB Resolution 6/2023): the precautionary block of the Special Refund
  Mechanism, which also has a deadline.
- **LGPD** (Law 13.709/2018, art. 37): flagging someone as a risk is processing of
  personal data and requires an auditable record. It is exactly the document the
  same transaction wrote.

The argument closes like this: if the flagging were not atomic, that report could
describe a state that never existed — accounts blocked with no audit, or audit with
no block.

The endpoint also refuses to open a second case over nodes that already belong to
an open one. That is not just a guard rail, it is the correct banking behaviour:
you do not open two cases on the same people, you reopen the one that exists.

### 10. Real time, and the A/B that proves it is not a trick (3 min)
**Inject transaction into the network.** The change stream fires and the alert
appears with the lookup latency attached. No polling, no scheduled job.

Now the part that is worth it: click **Close case** and inject the same transaction
again. **No alert** — but the tab does not go empty: a grey line appears saying the
transaction was *checked*, how many accounts the listener looked at and in how many
milliseconds. The screen also says, in green, that silence is the correct outcome.

That line exists because silence must not look like a broken demo. It is the visible
proof that the listener woke up, read the state and decided not to alert.

Do it in the reverse order if you prefer (inject with the network free, flag, inject
again). Either way the point is the same: the alert consults the real state of the
accounts, it does not fire because the demo told it to. It is the question that
always comes up, and answering it before it is asked is worth more than answering it
afterwards.

**Reset demo** returns everything to the initial state between presentations.

### 11. The graph maintains itself (1 min)
If someone asks how the edges stay fresh in production — and they usually do — call
`POST /api/demo/link-accounts`. It inserts two transactions that make two people
with no connection start using the same device.

Before: `GET /api/connections/between?a=…&b=…` answers `connected: false`. About two
seconds later, `true`, with `source: "change_stream"`. And the next expansion
already walks the new edge.

Say what that path does not do as well: it adds and updates edges, it does not prune
retroactively and it does not backfill. The correct pattern is both, change stream
for freshness and batch for consistency. It is in
`docs/adr/0003-manutencao-incremental-de-arestas.md`.

### 12. Closing (1-2 min)
One less system to operate, synchronise, back up and secure. Offer `COMPETITIVE.md`
to anyone who wants to go deeper into when this **does not** apply.

## Frequently asked questions, with prepared answers

**"Does this scale to billions of records?"**
Do not promise unlimited scale, and bring numbers instead of adjectives. Measured on
a graph of 2.4 million edges: two hops reach 21,000 nodes in 254 ms, and three hops
**do not finish** without pruning — they exceed the 100 MB limit `$graphLookup` has
for assembling its result. With pruning, the same traversal takes one second.

There is also the sharding constraint: `$graphLookup` does not read in a distributed
way from a sharded collection. For larger volumes the pattern changes — isolate the
graph collections as unsharded, or pre-compute subgraphs. See `LIMITATIONS.md`,
sections 1 and 5.

**"Do you have native PageRank / community detection?"**
No. Explain the Spark/NetworkX integration pattern in `LIMITATIONS.md §3` and ask
whether the use case needs that running continuously or whether a periodic analysis
solves the business problem.

**"Why not just use a graph database?"**
Sometimes you should — `COMPETITIVE.md` lists exactly when. The question to ask back
is whether the graph is the product or a lens over a product that already exists.

**"The data is synthetic."**
It is, and the topology was designed on purpose; `docs/adr/0001-topologia-do-dado-sintetico.md`
records the three alternatives that were discarded by measurement. What the topology
does not do is hide degradation — that is step 6.
