# ADR 0002 — 512 dimensions, quantised, on a collection of 32 documents

**Status:** accepted · **Date:** 2026-08-26 (rewritten 2026-08-28, when the vectors
moved off `companies`)

## Context

The concentration analysis asks whether N different activity codes are really N
different businesses. That is a comparison of **meaning between activity
descriptions**, and it needs embeddings.

The original decision was about capacity: every company needed a vector, that was
1.2 million vectors, and the demo cluster is a shared **M20 (4 GB RAM)**. Vector
search wants the HNSW graph resident in memory, and a 1.2 M × 1024-dimension
float32 index does not fit next to everything else the cluster is doing.

## The decision that mattered more than any tuning

**Put the vectors on `activities` — one document per distinct description — not on
`companies`.**

The base has **32 distinct activity descriptions** across 1.2 million companies.
Embedding the field on every company and indexing that meant `$vectorSearch`
walking 1.2 M documents, with `numCandidates: 10000`, to compare 32 texts:
**29.3 seconds** per query. On the small collection the same answer is
milliseconds.

That reframes the capacity question entirely: the index is now 32 vectors, and it
would fit on any tier. The dimension and quantisation choices below stopped being
survival measures and became merely sensible.

## Dimensions and quantisation

| Option | Index size at 1.2 M vectors | At 32 vectors |
|---|---|---|
| 1024d float32 | ~4.9 GB — does not fit an M20 | negligible |
| 512d float32 | ~2.5 GB — still uncomfortable | negligible |
| **512d + scalar quantisation** | **~0.6 GB** | negligible |

`voyage-3-lite` at `output_dimension: 512`, stored as BinData float32, with
`quantization: "scalar"` on the index. Kept after the move because:

- it is what was measured, and the measurement is reproducible;
- the recall loss on this task is nil — the pairs the panel needs to separate sit
  at 0.89–1.00 similarity within a sector and well below 0.80 across sectors, and
  scalar quantisation does not move a gap that wide;
- if a customer's registry has free-text activity descriptions, the distinct set
  is much larger than 32, and the sizing argument comes straight back.

## Consequence

Cost of embedding is proportional to **distinct texts**, not to rows: 32 calls to
the Voyage API for the whole base. Say that out loud in the demo, and say the
caveat with it — a real free-text registry deduplicates far less.

## Two operational notes

**Order.** `embed_activities.py` runs after the ownership base exists. Run it
earlier and there are no descriptions to embed, so the panel comes back empty with
no error to explain why.

**Idempotence.** The script only embeds descriptions that are not in `activities`
yet, so re-running after a data reload costs nothing and fills the gaps. `--force`
re-embeds everything, which is what to use after changing model or dimension.
