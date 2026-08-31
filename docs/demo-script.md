# Presentation script

Two scenarios, one database, about twenty minutes. Both are the same mechanism —
`$graphLookup` over a shallow tree — applied to two questions a bank asks every
day.

## Before you start

1. `curl -s localhost:8350/health | jq` returns `"status": "ok"`. One call
   validates the Atlas connection, the collection counts, the Search/Vector index
   status and the latency of a reference `$graphLookup`.
2. If it comes back `degraded`, `checks` says exactly what. An index still
   `BUILDING` is not a blocker — the ownership traversal and the hierarchy work
   without it; skip the Concentration step.
3. `POST /api/demo/reset` so nothing is left flagged from an earlier session. It
   also removes edges created live in the last step, so its "before" is true again.
4. Open the UI once beforehand. The first `/api/entry-points` call takes about a
   second and the rest are instant — take that second off the critical path.
5. Note one applicant CNPJ from the selector. If the UI fails live, the same
   numbers come out of `curl localhost:8350/api/group/<cnpj>?depth=3`.
6. If you have just reloaded the base, **restart the backend**: the change-stream
   listener resumes from its stored token and spends time draining the oplog.

## Part 1 — the credit decision (about 10 minutes)

**Step 1. The question, before any screen.** A company asks for credit. On its own
record it looks small and clean: rating A, no arrears. Approve?

**Step 2. The company alone.** Search the CNPJ, depth 1. You see the company and
whoever controls it directly. Nothing alarming. This is what the analyst sees in
the core banking system today.

**Step 3. Raise the depth.** Each applicant in the picker belongs to a group with a
**different** ownership depth — the dropdown says which, from 1 to 6 levels. On the
six-level one, every step of the control reveals companies the previous step did
not: at 2 the holding appears, at 6 the whole conglomerate does — 43 companies,
including subsidiaries the applicant's record never mentions. Say the number out
loud: each of those answers came from **one** aggregation against the cluster, and
none of them took more than 23 ms of p50 against 1.2 M companies and 2.5 M stakes.

Then ask for depth 6 on the one-level group. Same answer as depth 1, same latency:
the traversal ends when the tree ends, not when the budget does.

**Step 4. The consolidated figure.** The panel sums limit, drawn and overdue over
every entity the traversal reached. That number is the credit decision, and it
does not exist in any single record. Point at the sibling in another branch that
carries the arrears: the applicant is clean, the group is not.

**Step 5. The concentration.** Eight different CNAE codes look like
diversification, and diversification is what dilutes risk. "Building
construction", "masonry works" and "engineering services for construction" are
three codes and one business. Comparing codes misses it; comparing words misses it
too — the phrases share no term. Comparing meaning catches it. That is Atlas
Vector Search, in the same database, over the same documents.

**Step 6. The bridge shareholder.** In the showcase groups, one individual holds a
minority stake in two conglomerates the registry treats as unrelated. This is the
finding the analyst cannot reach by reading records one at a time.

**Step 7. Open the review.** One multi-document ACID transaction flags the whole
group and opens the case. Try to open it twice: you get a 409 with the existing
`case_id`, not a duplicate.

**Step 8. Who else finds out, and when.** Opening the review marked dozens of
companies in one transaction. Ask the question before showing the answer: *the desk
that runs limits, the monitoring team, the analyst on the next case — how do they
learn this group is now blocked?*

Switch to the Alerts tab. The event is already there: it arrived on its own, with
no polling and no scheduled job. It names the case, how many companies entered
review, and the exposure they add up to — and it reports the milliseconds between
the transaction committing and the event landing.

Then close the review. The counterpart event arrives by the same path, in the same
time window. That A/B is the point: the listener reads state instead of firing on
its own.

Worth naming: `update_many` over 40 companies produces 40 change events, not one.
The listener coalesces them by case and publishes a single event with the real
count. In most banks this window is covered by a nightly batch. Here it is the same
database emitting the event.

## Part 2 — who may see what (about 5 minutes)

This is the second shape of the same mechanism, and it lands with anyone who runs
a commercial desk.

**Step 9. Pick a manager.** The Visibility tab lists a regional, a manager and two
advisors. Pick the manager: the scope panel shows how many advisors sit below them
and the consolidated book of all of them. One aggregation: `$graphLookup` walks
down `advisors.reports_to`, and the book is joined on the server.

**Step 10. Pick an advisor.** Scope drops to one person and the book shrinks to
their own clients. Nothing on the client asked for less data — the server derived
a smaller scope from who the user is.

**Step 11. The boundary, on the company on screen.** The same CNPJ is visible to
the manager and refused to the advisor in the other branch, with the reason
spelled out. In the first showcase group the members are deliberately split
between two advisors under different managers, so the visibility boundary cuts
through the middle of an economic group.

**Step 12. Say why it is a traversal and not a list.** The common alternative is a
pre-computed list of accounts each user may see. It goes stale on every book
transfer, and between the event and the recompute somebody sees what they should
not. Here the tree is the source of truth and the answer is always derived from
its current state.

## The closing argument

Say it plainly, and say the limit out loud with it:

- Both answers came from the database the data already lives in. No second engine,
  no synchronisation pipeline, no second backup and DR surface.
- The tree is shallow and the question is a point lookup by business key. That is
  the pattern where this wins — `COMPETITIVE.md` opens with the table that says
  when it does not.
- The cost that matters here is not the traversal: it is loading the base and
  operating it. `queries/benchmarks.md` has both numbers, measured, with the
  command that reproduces them.

## One habit worth having on screen

Every list in the right-hand rail points at the graph. Click a search hit, an
activity, a user, an alert — the nodes it refers to light up, everything else dims,
and the canvas centres on them. Use it instead of describing where something is:
"this activity, these three companies" reads far better than a finger on the screen.

Click the same row again to release the highlight.

## Proving it holds up, before you present

```bash
.venv/bin/python tests/test_resilience.py     # 66 hostile cases
.venv/bin/python tests/stress.py              # mixed workload up to 64 concurrent
```

The stress run mixes the five paths of the demo in the proportion a real session
uses — mostly traversal, some search, little semantic analysis — and checks five
things: no 5xx, no client-side connection failures, p95 under the declared ceiling,
`/health` still `ok` afterwards, and **the consolidated figure of a group
unchanged** after the load. The last one is the one that matters: a number that
drifts under concurrency is worse than a slow one.

## If something fails live

| Symptom | What to do |
|---|---|
| Search or Concentration returns 503 | the index is `BUILDING`; that panel degrades, the screen does not. Skip steps 5–6 |
| The traversal times out | drop the depth; `LIMITATIONS.md §5` explains the 100 MB ceiling |
| No alert appears | restart the backend (oplog draining), then repeat step 8 |
| The UI does not load | `curl localhost:8350/api/group/<cnpj>?depth=3` gives the same numbers |
