# ADR 0002 — Vetores em 512d, BinData float32, índice quantizado

**Status:** aceito · **Data:** 2026-08-26

## Contexto

Toda transação precisa de `reason_embedding` para o passo de similaridade
semântica. São 600 mil transações. O cluster da demo é um **M20 (4 GB de RAM,
150 GB de disco)** compartilhado com outras aplicações, que já ocupam cerca de
60 GB.

## Números que decidiram

| Opção | Vetor em disco | Grafo HNSW residente |
|---|---|---|
| 1024d, array de `double` | ~4,9 GB | ~2,4 GB |
| 512d, array de `double` | ~2,4 GB | ~1,2 GB |
| 512d, BinData `float32` | ~1,2 GB | ~1,2 GB |
| **512d, BinData `float32`, `quantization: scalar`** | **~1,2 GB** | **~0,3 GB** |

## Decisão

`voyage-3-lite` em 512 dimensões, gravado como `Binary.from_vector(...,
BinaryVectorDtype.FLOAT32)`, com `quantization: "scalar"` na definição do índice.

Além disso, o embedder deduplica: `reason_text` vem de um pool de templates, então
`embed_reasons.py` embeda os **textos distintos** e propaga o vetor por
`update_many`. Toda transação fica com vetor; a Voyage é chamada algumas dezenas
de vezes em vez de ~6.250.

## Consequência

Cabe no cluster compartilhado sem prejudicar as outras PoVs. A quantização escalar
tem custo de recall, aceitável aqui porque a demonstração é qualitativa (dois
textos equivalentes se encontram), não uma avaliação de precisão.

**O que dizer ao cliente:** a deduplicação é honesta, não um truque — num dataset
real de campo livre ela renderia bem menos, e o custo é proporcional aos textos
distintos, não às linhas. Vale dizer isso em voz alta na demo.
