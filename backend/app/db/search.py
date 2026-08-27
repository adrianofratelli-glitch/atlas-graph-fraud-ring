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


def resolve_entity(
    query: str,
    limit: int = 10,
    person_ids: list[str] | None = None,
    ring_ids: list[str] | None = None,
    scope: str = "base",
) -> dict[str, Any]:
    """Entity resolution difusa em `people.name`.

    `compound.should` com três cláusulas: termo exato (pontua mais alto), fuzzy
    com `maxEdits: 2` (pega o erro de digitação) e autocomplete (pega digitação
    parcial). O `$graphLookup` por igualdade nunca acharia o gêmeo com typo — é
    por isso que este passo existe no roteiro.

    ## Escopo, e por que o padrão é a base inteira

    Buscar "Diego" e receber Diegos que não têm nada a ver com a investigação é
    ruído — daí o escopo `rede`, que restringe aos nós que estão na tela.

    Mas o padrão continua sendo `base`, de propósito: **o caso que dá valor a
    este painel é justamente um registro que o grafo NÃO alcança.** O gêmeo com
    erro de digitação não tem aresta nenhuma ligando-o à rede; se a busca fosse
    sempre escopada, ele nunca apareceria e o passo perderia o sentido.

    A saída resolve o ruído de outro jeito: todo resultado diz se está **na rede**
    da tela e a quantos saltos, ou fora dela. O que estiver na rede vem primeiro.
    """
    s = get_settings()
    _require("people", s.search_index)

    compound: dict[str, Any] = {
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
    }

    escopado = scope == "rede" and bool(person_ids)
    # `ring_id` é `token` no índice, então o filtro roda DENTRO do `$search` — o
    # servidor nem pontua quem está fora da rede. Sem anel (rede da população
    # limpa) não há token para filtrar, e sobra o `$match` por `_id` abaixo.
    if escopado and ring_ids:
        compound["filter"] = [{"in": {"path": "ring_id", "value": ring_ids}}]

    pipeline: list[dict[str, Any]] = [{"$search": {"index": s.search_index, "compound": compound}}]
    if escopado:
        # Segunda guarda, exata: só os nós que estão de fato na tela. O `$limit`
        # vem depois, senão o corte aconteceria antes do filtro.
        pipeline.append({"$limit": max(limit * 40, 400)})
        pipeline.append({"$match": {"_id": {"$in": person_ids}}})
    pipeline.append({"$limit": limit})
    pipeline.append(
        {
            "$project": {
                "name": 1,
                "ring_id": 1,
                "risk_flags": 1,
                "near_duplicate_of": 1,
                "city": {"$first": "$addresses.city"},
                "score": {"$meta": "searchScore"},
            }
        }
    )

    started = time.perf_counter()
    results = with_retry(lambda: list(get_db().people.aggregate(pipeline)), "resolve_entity")

    na_tela = set(person_ids or [])

    # No modo `base`, ordenar depois não basta.
    #
    # O `$search` corta pelos 10 mais relevantes ANTES de a gente olhar quem está
    # na rede. Buscar "Diego" numa base de 150 mil devolve dez Diegos aleatórios,
    # e o Diego que está na tela — o único que interessa — fica de fora do corte.
    # Reordenar não conserta o que nunca chegou.
    #
    # Então roda-se uma segunda passada, escopada na rede (barata: o filtro por
    # `ring_id` é token no índice, e a rede tem dezenas de nós), e o resultado
    # dela entra na frente. A base continua ali embaixo, que é o que mantém o
    # gêmeo com erro de digitação alcançável.
    if not escopado and na_tela and ring_ids:
        da_rede = resolve_entity(
            query, limit=limit, person_ids=person_ids, ring_ids=ring_ids, scope="rede"
        )["results"]
        vistos = {r["_id"] for r in da_rede}
        results = da_rede + [r for r in results if r["_id"] not in vistos]

    for r in results:
        r["na_rede"] = r["_id"] in na_tela
    results.sort(key=lambda r: (not r["na_rede"], -r["score"]))

    return {
        "query": query,
        "index": s.search_index,
        "scope": "rede" if escopado else "base",
        "scoped_to": len(na_tela) if escopado else None,
        "results": results[: limit * 2 if not escopado else limit],
        "na_rede": sum(1 for r in results if r["na_rede"]),
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


def similar_reasons(
    text: str,
    limit: int = 8,
    ring_only: bool = False,
    ring_ids: list[str] | None = None,
    scope: str = "rede",
) -> dict[str, Any]:
    """Vector Search sobre `transactions.reason_embedding`.

    ## Que pergunta este painel responde

    Solto sobre a base inteira, ele vira uma curiosidade: "o motor entende
    sinônimo". Verdade, mas não é trabalho de investigação, e a plateia sente isso.

    Escopado na rede que está na tela, ele responde algo que o analista realmente
    pergunta: **"que justificativas essas contas usam para mover dinheiro?"** A
    resposta útil não é uma frase, é o padrão — a mesma desculpa reescrita de N
    formas diferentes pelos membros do anel. Busca por palavra-chave não agrupa
    isso, porque as frases não dividem palavra nenhuma.

    `ring_id` é campo de filtro do índice vetorial, então o escopo roda dentro do
    `$vectorSearch`: o ANN só percorre os vetores da rede, em vez de achar 400
    vizinhos na base e descartar quase todos depois.
    """
    s = get_settings()
    _require("transactions", s.vector_index)
    query_vector = embed_query(text)

    # `reason_text` vem de um pool de templates, então milhares de transações
    # dividem o mesmo texto — e o mesmo vetor. Sem agrupar, o painel devolveria a
    # mesma frase seis vezes. Buscamos um bloco maior e colapsamos por texto,
    # mantendo o melhor score e a contagem de ocorrências (que é informação útil:
    # "esse motivo aparece 24 mil vezes na base").
    escopado = scope == "rede" and bool(ring_ids)
    fetch = max(limit * 40, 400)
    # `numCandidates` alto no modo escopado não é chute.
    #
    # O `$vectorSearch` percorre o grafo HNSW da coleção INTEIRA e aplica o filtro
    # durante a travessia. Um filtro muito seletivo — uma rede de 30 pessoas
    # dentro de 600 mil transações — faz quase todo candidato ser descartado, e
    # com `numCandidates` baixo a busca esgota a lista antes de juntar resultado.
    # Medido: com 360 candidatos a rede devolvia **um** motivo; o dado tinha nove.
    #
    # É a armadilha clássica de ANN com filtro seletivo, e vale contar na demo:
    # a poda por filtro é barata de escrever e cara de dimensionar.
    #
    # 10.000 é o teto do servidor (`"numCandidates" must be within bounds
    # [1..10000]`), não um número escolhido — o que também é o limite prático de
    # quão seletivo um filtro pode ser aqui. Ver LIMITATIONS.md.
    MAX_CANDIDATES = 10_000
    candidates = MAX_CANDIDATES if escopado else min(fetch * 3, MAX_CANDIDATES)
    stage: dict[str, Any] = {
        "index": s.vector_index,
        "path": "reason_embedding",
        "queryVector": Binary.from_vector(query_vector, BinaryVectorDtype.FLOAT32),
        "numCandidates": candidates,
        "limit": fetch,
    }
    if escopado:
        stage["filter"] = {"ring_id": {"$in": ring_ids}}
    elif ring_only:
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
        "scope": "rede" if escopado else "base",
        "scoped_to": ring_ids if escopado else None,
        # Quantas maneiras diferentes de dizer a mesma coisa vieram — é o número
        # que sustenta a leitura, não o score de cada linha.
        "formas_distintas": len(results),
        "results": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
