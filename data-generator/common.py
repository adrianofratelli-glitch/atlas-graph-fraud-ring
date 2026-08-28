"""Infra compartilhada pelos scripts do gerador.

Duas invariantes vivem aqui e não devem ser contornadas:

1. Todo `_id` é determinístico (uuid5 sobre atributos-chave). É isso que torna o
   gerador idempotente: rodar duas vezes reescreve o mesmo documento em vez de
   criar um novo.
2. Toda escrita passa por `bulk_replace`, que usa `ReplaceOne(upsert=True)`.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from dotenv import load_dotenv
from pymongo import MongoClient, ReplaceOne
from pymongo.collection import Collection
from pymongo.database import Database

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

NAMESPACE = uuid.UUID("6f6b8b6e-3d0f-5a4a-9a1e-2f0b7c4d1e55")

BATCH_SIZE = 2000


def det_id(kind: str, *parts: Any) -> str:
    """_id determinístico. Mesmos `parts` => mesmo _id, sempre."""
    return f"{kind}_{uuid.uuid5(NAMESPACE, '|'.join(str(p) for p in parts))}"


def get_db() -> Database:
    uri = os.environ["MONGODB_URI"]
    client = MongoClient(uri, serverSelectionTimeoutMS=10_000, retryWrites=True)
    client.admin.command("ping")
    return client[os.getenv("MONGODB_DB", "graph_grupo_economico")]


def chunked(it: Iterable[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    buf: list[Any] = []
    for item in it:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def bulk_replace(coll: Collection, docs: Iterable[dict], label: str = "") -> int:
    """Upsert idempotente. Retorna quantos documentos foram enviados."""
    total = 0
    for batch in chunked(docs):
        coll.bulk_write(
            [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in batch],
            ordered=False,
        )
        total += len(batch)
        if label:
            print(f"  {label}: {total:,}", end="\r", flush=True)
    if label:
        print(f"  {label}: {total:,}")
    return total


def bulk_insert(coll: Collection, docs: Iterable[dict], label: str = "") -> tuple[int, float]:
    """Carga limpa com `insert_many`. Devolve (documentos, segundos).

    `bulk_replace` usa `ReplaceOne(upsert=True)`, que é o certo para reexecutar o
    gerador sem duplicar — e é a primitiva errada para **medir ingestão**. O
    upsert paga uma busca por documento; uma carga inicial de verdade não faz
    isso, ela insere.

    Publicar vazão medida com upsert subestimaria o MongoDB e, pior, mediria algo
    que ninguém roda em produção. Este caminho existe só para a carga inicial
    (quando a coleção está vazia) e para o benchmark de carga.
    """
    total = 0
    t0 = time.perf_counter()
    for batch in chunked(docs):
        coll.insert_many(batch, ordered=False)
        total += len(batch)
        if label:
            print(f"  {label}: {total:,}", end="\r", flush=True)
    dt = time.perf_counter() - t0
    if label:
        print(f"  {label}: {total:,} em {dt:.1f}s ({total/max(dt, 1e-9):,.0f} docs/s)")
    return total, dt
