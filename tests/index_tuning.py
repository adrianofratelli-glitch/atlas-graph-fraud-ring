#!/usr/bin/env python3
"""Mede se um índice composto ajuda o traversal com poda.

Pergunta concreta: com `{from: 1}`, o filtro de `restrictSearchWithMatch` sobre
`weight` é aplicado no estágio FETCH — o servidor busca o documento e só então
descarta. Num nó de grau 400, são 400 documentos lidos para jogar fora.

Um índice `{from: 1, weight: 1}` limita a varredura dentro do próprio índice.
Este script mede se a diferença aparece de verdade, num grafo grande o bastante
para importar.

    ../.venv/bin/python tests/index_tuning.py
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data-generator"))
from common import get_db  # noqa: E402

from pymongo import ASCENDING  # noqa: E402
from pymongo.errors import OperationFailure  # noqa: E402

COLL = "connections_scale"
LIMIAR = 5


def entrada_de_grau_alto(db) -> str | None:
    """Nó de grau alto de verdade.

    `$sample` sobre 2,4 M de arestas subestima o grau: ele vê uma fração das
    arestas de cada nó. A escolha correta é agrupar o conjunto inteiro uma vez.
    """
    for d in db[COLL].aggregate(
        [{"$group": {"_id": "$from", "g": {"$sum": 1}}}, {"$sort": {"g": -1}}, {"$limit": 1}],
        allowDiskUse=True,
    ):
        return d["_id"]
    return None


def medir(db, entrada: str, prof: int, runs: int = 5) -> dict:
    pipeline = [
        {"$match": {"_id": entrada}},
        {"$graphLookup": {
            "from": COLL, "startWith": "$_id", "connectFromField": "to",
            "connectToField": "from", "as": "net", "maxDepth": prof, "depthField": "hops",
            "restrictSearchWithMatch": {"weight": {"$lte": LIMIAR}},
        }},
        {"$project": {"nos": {"$size": {"$setUnion": ["$net.to", []]}}}},
    ]
    amostras, ultimo = [], None
    for k in range(runs + 1):
        t0 = time.perf_counter()
        try:
            ultimo = list(db.people.aggregate(pipeline, allowDiskUse=True))
        except OperationFailure as exc:
            return {"erro": str(exc).split(",")[0]}
        if k:
            amostras.append((time.perf_counter() - t0) * 1000)
    return {
        "nos": ultimo[0]["nos"] if ultimo else 0,
        "media_ms": round(statistics.mean(amostras), 1),
        "p95_ms": round(sorted(amostras)[-1], 1),
    }


def plano(db, entrada: str) -> str:
    r = db.command({"explain": {"find": COLL, "filter": {"from": entrada, "weight": {"$lte": LIMIAR}}},
                    "verbosity": "executionStats"})
    st = r["executionStats"]
    import json as _json
    nome = _json.dumps(r["queryPlanner"]["winningPlan"])
    idx = "from_1_weight_1" if "from_1_weight_1" in nome else "from_1"
    return f"índice {idx}: {st['nReturned']} devolvidos, {st['totalKeysExamined']} chaves, {st['totalDocsExamined']} docs"


def main() -> None:
    db = get_db()
    if db[COLL].estimated_document_count() == 0:
        raise SystemExit(f"{COLL} vazia — rode tests/scale_graph.py --build antes.")

    entrada = entrada_de_grau_alto(db)
    if not entrada:
        raise SystemExit("não achei nó de grau adequado")
    grau = db[COLL].count_documents({"from": entrada})
    print(f"entrada de grau {grau}\n")

    nomes = {i["name"] for i in db[COLL].list_indexes()}
    if "from_1_weight_1" in nomes:
        db[COLL].drop_index("from_1_weight_1")
        print("(índice composto pré-existente removido para medir a linha de base)")

    print("── com {from: 1} apenas")
    print(f"   {plano(db, entrada)}")
    antes = {p: medir(db, entrada, p) for p in (1, 2)}
    for p, m in antes.items():
        print(f"   depth {p}: {m.get('nos', '—')} nós · {m.get('media_ms', m.get('erro'))} ms")

    print("\ncriando {from: 1, weight: 1}…")
    t0 = time.perf_counter()
    db[COLL].create_index([("from", ASCENDING), ("weight", ASCENDING)], name="from_1_weight_1")
    print(f"   construído em {time.perf_counter() - t0:.1f}s")

    print("\n── com {from: 1, weight: 1}")
    print(f"   {plano(db, entrada)}")
    depois = {p: medir(db, entrada, p) for p in (1, 2)}
    for p, m in depois.items():
        print(f"   depth {p}: {m.get('nos', '—')} nós · {m.get('media_ms', m.get('erro'))} ms")

    print("\n── veredito")
    for p in (1, 2):
        a, d = antes[p].get("media_ms"), depois[p].get("media_ms")
        if a and d:
            delta = (a - d) / a * 100
            print(f"   depth {p}: {a:.1f} ms -> {d:.1f} ms  ({delta:+.0f}%)")
        else:
            print(f"   depth {p}: antes={antes[p]} depois={depois[p]}")

    tam = db.command("collstats", COLL)
    print(f"\n   custo do índice: {tam['indexSizes'].get('from_1_weight_1', 0) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
