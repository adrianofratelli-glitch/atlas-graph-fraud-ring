"""Infra compartilhada pelos scripts do gerador.

Duas invariantes vivem aqui e não devem ser contornadas:

1. Todo `_id` é determinístico (uuid5 sobre atributos-chave). É isso que torna o
   gerador idempotente: rodar duas vezes reescreve o mesmo documento em vez de
   criar um novo.
2. Toda escrita passa por `bulk_replace`, que usa `ReplaceOne(upsert=True)`.
"""
from __future__ import annotations

import os
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
    return client[os.getenv("MONGODB_DB", "graph_fraud_ring")]


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
