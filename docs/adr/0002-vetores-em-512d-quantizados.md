# ADR 0002 — 512d vectors, BinData float32, quantised index

**Status:** accepted · **Date:** 2026-08-26

## Context

Every transaction needs a `reason_embedding` for the semantic similarity step.
That is 600,000 transactions. The demo cluster is an **M20 (4 GB RAM, 150 GB
disk)** shared with other applications, which already occupy about 60 GB.

## The numbers that decided it

| Option | Vector on disk | Resident HNSW graph |
|---|---|---|
| 1024d, array of `double` | ~4.9 GB | ~2.4 GB |
| 512d, array of `double` | ~2.4 GB | ~1.2 GB |
| 512d, BinData `float32` | ~1.2 GB | ~1.2 GB |
| **512d, BinData `float32`, `quantization: scalar`** | **~1.2 GB** | **~0.3 GB** |

## Decision

`voyage-3-lite` at 512 dimensions, stored as `Binary.from_vector(...,
BinaryVectorDtype.FLOAT32)`, with `quantization: "scalar"` in the index definition.

On top of that, the embedder deduplicates: `reason_text` comes from a pool of
templates, so `embed_reasons.py` embeds the **distinct texts** and propagates the
vector with `update_many`. Every transaction ends up with a vector; Voyage is
called a few dozen times instead of ~6,250.

## Consequence

It fits on the shared cluster without hurting the other projects on it. Scalar
quantisation costs recall, which is acceptable here because the demonstration is
qualitative (two equivalent texts find each other), not a precision evaluation.

**What to tell the customer:** the deduplication is honest, not a trick — on a real
free-text dataset it would yield much less, and the cost is proportional to the
number of distinct texts, not to the number of rows. Worth saying out loud in the
demo.

## Two operational notes added later

**Order in the pipeline.** `embed_reasons.py` has to run *after* the rings are
injected. It used to run before, and the ring transactions were born without
vectors — 11 of 137 vectorised in one ring — which made the semantic panel scoped
to a network come back almost empty.

**The backfill needs an index.** The propagation is one `update_many` per distinct
text, filtering on `reason_embedding: {$exists: false}`. Without
`{reason_text: 1, reason_embedding: 1}` that is 33 collection scans of 600,000
documents, and the backfill takes tens of minutes.
