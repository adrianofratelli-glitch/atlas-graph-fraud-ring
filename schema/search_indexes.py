#!/usr/bin/env python3
"""Cria (ou atualiza) os índices de Atlas Search e Atlas Vector Search.

Não são índices de banco: vivem no serviço de busca do Atlas, têm ciclo de vida
próprio (`PENDING` -> `BUILDING` -> `READY`) e por isso não estão em
`schema/indexes.js`. O `/health` do backend checa o status deles, e o frontend
degrada com um badge quando não estão `READY` — ver README, seção Resiliência.

Uso:
    python schema/search_indexes.py            # cria/atualiza e espera ficar READY
    python schema/search_indexes.py --status   # só relata o estado atual
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SEARCH_INDEX = os.getenv("ATLAS_SEARCH_INDEX_NAME", "people_entity_resolution")
VECTOR_INDEX = os.getenv("VECTOR_INDEX_NAME", "transactions_reason_vector")
DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", 512))

# `name` recebe três analisadores: o padrão para busca por termo, `autocomplete`
# para digitação parcial e `keyword` (via string) para casamento exato. É a
# combinação que resolve o gêmeo com erro de digitação do passo 5 do roteiro.
SEARCH_DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "name": [
                {"type": "string", "analyzer": "lucene.standard"},
                {"type": "autocomplete", "minGrams": 3, "maxGrams": 12, "tokenization": "edgeGram"},
            ],
            "document_id": {"type": "string", "analyzer": "lucene.keyword"},
            "addresses": {
                "type": "document",
                "fields": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                    "zip": {"type": "string", "analyzer": "lucene.keyword"},
                },
            },
            "ring_id": {"type": "token"},
        },
    }
}

# `quantization: scalar` não é detalhe de tuning — é requisito de capacidade.
# 600k vetores de 512 dimensões em float32 pesam ~1,2 GB residentes; o cluster de
# demonstração é um M20 (4 GB) compartilhado com as outras PoVs do portfólio.
# A quantização escalar reduz o grafo HNSW para ~1/4 disso. Ver LIMITATIONS.md.
VECTOR_DEFINITION = {
    "fields": [
        {
            "type": "vector",
            "path": "reason_embedding",
            "numDimensions": DIMENSIONS,
            "similarity": "cosine",
            "quantization": "scalar",
        },
        {"type": "filter", "path": "ring_id"},
        {"type": "filter", "path": "amount"},
    ]
}


def ensure(coll, name: str, definition: dict, kind: str) -> None:
    existing = {i["name"]: i for i in coll.list_search_indexes()}
    if name in existing:
        coll.update_search_index(name, definition)
        print(f"  ~ {kind} `{name}` atualizado")
        return
    coll.create_search_index(SearchIndexModel(definition=definition, name=name, type=kind))
    print(f"  + {kind} `{name}` criado")


def status(db) -> dict[str, str]:
    out: dict[str, str] = {}
    for coll, name in ((db.people, SEARCH_INDEX), (db.transactions, VECTOR_INDEX)):
        found = next((i for i in coll.list_search_indexes() if i["name"] == name), None)
        out[name] = found["status"] if found else "MISSING"
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args()

    client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=10_000)
    db = client[os.getenv("MONGODB_DB", "graph_fraud_ring")]

    if args.status:
        for name, st in status(db).items():
            print(f"  {name:38s} {st}")
        return

    print(f"banco: {db.name}")
    ensure(db.people, SEARCH_INDEX, SEARCH_DEFINITION, "search")
    ensure(db.transactions, VECTOR_INDEX, VECTOR_DEFINITION, "vectorSearch")

    print("\naguardando READY (o build roda no serviço de busca, não no mongod)...")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        st = status(db)
        print("  " + " | ".join(f"{k.split('_')[0]}={v}" for k, v in st.items()), end="\r", flush=True)
        if all(v == "READY" for v in st.values()):
            print("\n✅ ambos READY")
            return
        if any(v == "FAILED" for v in st.values()):
            print(f"\n❌ build falhou: {st}")
            sys.exit(1)
        time.sleep(10)
    print(f"\n⚠️  timeout de {args.timeout}s. Estado atual: {status(db)}")
    sys.exit(2)


if __name__ == "__main__":
    main()
