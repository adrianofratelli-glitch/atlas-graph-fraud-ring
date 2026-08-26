#!/usr/bin/env python3
"""Gera `reason_embedding` para todas as transações.

Decisão que vale explicar em demo: `reason_text` vem de um pool de templates, e
600 mil transações contêm algumas dezenas de textos distintos. Embeddar por
documento seriam ~4.700 chamadas à Voyage para produzir ~30 vetores diferentes.
O script embeda os **textos únicos** e escreve o vetor correspondente em cada
transação por `update_many` — toda transação fica com vetor, com um punhado de
chamadas de API.

Isso é honesto na demo, não um truque: num dataset real de campo livre a
deduplicação renderia bem menos, e o custo real é proporcional aos textos
distintos, não às linhas. Vale dizer isso ao cliente.

O vetor é gravado como BinData float32 (`bson.binary.Vector`), não como array de
`double`: metade do tamanho no disco e no wire, e o Atlas Vector Search indexa
BinData nativamente.

Uso:
    python data-generator/embed_reasons.py
"""
from __future__ import annotations

import argparse
import os
import time

import httpx
from bson.binary import Binary, BinaryVectorDtype

from common import get_db

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
BATCH = 96


def embed(texts: list[str], model: str, dims: int, api_key: str) -> list[list[float]]:
    out: list[list[float]] = []
    with httpx.Client(timeout=60.0) as client:
        for i in range(0, len(texts), BATCH):
            chunk = texts[i : i + BATCH]
            for attempt in range(4):
                r = client.post(
                    VOYAGE_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"input": chunk, "model": model, "output_dimension": dims, "input_type": "document"},
                )
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            else:
                raise RuntimeError("Voyage recusou o lote após 4 tentativas (429).")
            out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="reescreve mesmo onde já existe vetor")
    args = p.parse_args()

    model = os.getenv("EMBEDDING_MODEL", "voyage-3-lite")
    dims = int(os.getenv("EMBEDDING_DIMENSIONS", 512))
    api_key = os.environ["VOYAGE_API_KEY"]

    db = get_db()
    texts = sorted(t for t in db.transactions.distinct("reason_text") if t)
    print(f"{len(texts)} textos distintos em {db.transactions.estimated_document_count():,} transações")

    print(f"embeddando com {model} ({dims}d)...")
    vectors = embed(texts, model, dims, api_key)

    written = 0
    for text, vec in zip(texts, vectors):
        flt = {"reason_text": text}
        if not args.force:
            flt["reason_embedding"] = {"$exists": False}
        res = db.transactions.update_many(
            flt, {"$set": {"reason_embedding": Binary.from_vector(vec, BinaryVectorDtype.FLOAT32)}}
        )
        written += res.modified_count
        print(f"  {written:,} transações atualizadas", end="\r", flush=True)

    faltando = db.transactions.count_documents({"reason_embedding": {"$exists": False}})
    print(f"\n✅ {written:,} escritas | sem vetor: {faltando:,}")


if __name__ == "__main__":
    main()
