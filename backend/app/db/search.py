"""Atlas Search e Atlas Vector Search.

O ponto da POV aqui não é que MongoDB faz busca — é que faz busca **no mesmo
motor** que guarda o dado do grafo, então uma "quase-conexão" (nome com erro de
digitação, motivo semanticamente equivalente) não exige um segundo sistema.

Ambas as funções degradam explicitamente: se o índice não existe ou ainda está
`BUILDING`, elas levantam `IndexUnavailable`, a rota devolve 503 e o frontend
mostra um badge. O `$graphLookup` continua funcionando — ver README.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from bson.binary import Binary, BinaryVectorDtype

from app.config import get_settings
from app.db.client import get_db, with_retry


class IndexUnavailable(RuntimeError):
    def __init__(self, index_name: str, status: str) -> None:
        self.index_name = index_name
        self.status = status
        super().__init__(f"índice `{index_name}` indisponível (status: {status})")


def index_status(collection: str, name: str) -> str:
    coll = get_db()[collection]
    try:
        found = next((i for i in coll.list_search_indexes() if i["name"] == name), None)
    except Exception:
        # `mongod` community não tem list_search_indexes. Não é erro de operação,
        # é ausência de capacidade — o frontend precisa distinguir os dois casos.
        return "UNSUPPORTED"
    return found["status"] if found else "MISSING"


def _require(collection: str, name: str) -> None:
    st = index_status(collection, name)
    if st != "READY":
        raise IndexUnavailable(name, st)


def resolve_entity(query: str, limit: int = 10) -> dict[str, Any]:
    """Entity resolution difusa em `people.name`.

    `compound.should` com três cláusulas: termo exato (pontua mais alto), fuzzy
    com `maxEdits: 2` (pega o erro de digitação) e autocomplete (pega digitação
    parcial). O `$graphLookup` por igualdade nunca acharia o gêmeo com typo — é
    por isso que este passo existe no roteiro.
    """
    s = get_settings()
    _require("people", s.search_index)
    pipeline = [
        {
            "$search": {
                "index": s.search_index,
                "compound": {
                    "should": [
                        {"text": {"query": query, "path": "name", "score": {"boost": {"value": 3}}}},
                        {
                            "text": {
                                "query": query,
                                "path": "name",
                                "fuzzy": {"maxEdits": 2, "prefixLength": 1},
                            }
                        },
                        {"autocomplete": {"query": query, "path": "name"}},
                    ],
                    "minimumShouldMatch": 1,
                },
            }
        },
        {"$limit": limit},
        {
            "$project": {
                "name": 1,
                "ring_id": 1,
                "risk_flags": 1,
                "near_duplicate_of": 1,
                "city": {"$first": "$addresses.city"},
                "score": {"$meta": "searchScore"},
            }
        },
    ]
    started = time.perf_counter()
    results = with_retry(lambda: list(get_db().people.aggregate(pipeline)), "resolve_entity")
    return {
        "query": query,
        "index": s.search_index,
        "results": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def embed_query(text: str) -> list[float]:
    s = get_settings()
    if not s.voyage_api_key:
        raise IndexUnavailable(s.vector_index, "NO_EMBEDDING_KEY")
    r = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {s.voyage_api_key}"},
        json={
            "input": [text],
            "model": s.embedding_model,
            "output_dimension": s.embedding_dimensions,
            "input_type": "query",
        },
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def similar_reasons(text: str, limit: int = 8, ring_only: bool = False) -> dict[str, Any]:
    """Vector Search sobre `transactions.reason_embedding`."""
    s = get_settings()
    _require("transactions", s.vector_index)
    query_vector = embed_query(text)

    # `reason_text` vem de um pool de templates, então milhares de transações
    # dividem o mesmo texto — e o mesmo vetor. Sem agrupar, o painel devolveria a
    # mesma frase seis vezes. Buscamos um bloco maior e colapsamos por texto,
    # mantendo o melhor score e a contagem de ocorrências (que é informação útil:
    # "esse motivo aparece 24 mil vezes na base").
    fetch = max(limit * 40, 400)
    stage: dict[str, Any] = {
        "index": s.vector_index,
        "path": "reason_embedding",
        "queryVector": Binary.from_vector(query_vector, BinaryVectorDtype.FLOAT32),
        "numCandidates": fetch * 3,
        "limit": fetch,
    }
    if ring_only:
        stage["filter"] = {"ring_id": {"$ne": None}}

    pipeline = [
        {"$vectorSearch": stage},
        {"$set": {"score": {"$meta": "vectorSearchScore"}}},
        {
            "$group": {
                "_id": "$reason_text",
                "score": {"$max": "$score"},
                "ocorrencias_no_bloco": {"$sum": 1},
                "amount": {"$avg": "$amount"},
                "ring_id": {"$max": "$ring_id"},
                "exemplo": {"$first": "$_id"},
            }
        },
        {"$sort": {"score": -1}},
        {"$limit": limit},
        {
            "$project": {
                "_id": "$exemplo",
                "reason_text": "$_id",
                "amount": {"$round": ["$amount", 2]},
                "ring_id": 1,
                "score": 1,
                "ocorrencias_no_bloco": 1,
            }
        },
    ]
    started = time.perf_counter()
    results = with_retry(lambda: list(get_db().transactions.aggregate(pipeline)), "similar_reasons")
    return {
        "query": text,
        "index": s.vector_index,
        "model": s.embedding_model,
        "dimensions": s.embedding_dimensions,
        "results": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
