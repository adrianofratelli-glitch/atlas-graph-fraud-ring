#!/usr/bin/env python3
"""Teste de escala do `$graphLookup`: um grafo grande e conectado de verdade.

Motivo de existir: as redes de fraude injetadas têm 30 membros e formam
componentes fechados. Elas provam que o traversal *encontra* a rede, mas não
provam nada sobre custo — o BFS nunca faz trabalho pesado. A pergunta que um
arquiteto faz primeiro é "e com o meu volume?", e responder com um anel de 30 nós
não convence ninguém.

Este script constrói `connections_scale`: um grafo de anexação preferencial sobre
as mesmas 150 mil pessoas, com ~1,2 milhão de arestas dirigidas e distribuição de
grau em cauda longa (como grafos reais de pagamento). Depois mede o traversal a
partir de nós de grau alto, médio e baixo.

    ../.venv/bin/python tests/scale_graph.py --build     # gera (demorado)
    ../.venv/bin/python tests/scale_graph.py --measure   # mede
    ../.venv/bin/python tests/scale_graph.py --drop      # limpa
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data-generator"))
from common import chunked, det_id, get_db  # noqa: E402

from pymongo import ASCENDING, ReplaceOne  # noqa: E402
from pymongo.errors import OperationFailure  # noqa: E402

COLL = "connections_scale"
SEED = 424242
NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


def build(db, n_people: int, avg_degree: int) -> None:
    """Anexação preferencial com teto de grau.

    Sem teto, o nó 0 vira um hub com centenas de milhares de arestas e o grafo
    deixa de parecer um grafo de pagamento — vira uma estrela. O teto reproduz o
    que a materialização real faz: hub acima do limiar não vira aresta.
    """
    rng = random.Random(SEED)
    coll = db[COLL]
    coll.drop()
    print(f"construindo {COLL}: {n_people:,} nós, grau médio alvo {avg_degree}")

    alvos: list[int] = [0, 1, 2]          # urna de anexação preferencial
    grau: dict[int, int] = {}
    teto = 400                            # mesmo espírito do HUB_FANOUT_THRESHOLD

    def edges():
        total = 0
        for i in range(3, n_people):
            for _ in range(avg_degree // 2):
                for _ in range(8):        # rejeita alvo saturado
                    j = alvos[rng.randrange(len(alvos))]
                    if j != i and grau.get(j, 0) < teto:
                        break
                else:
                    j = rng.randrange(i)
                a, b = det_id("person", i), det_id("person", j)
                grau[i] = grau.get(i, 0) + 1
                grau[j] = grau.get(j, 0) + 1
                alvos.append(i)
                alvos.append(j)
                peso = rng.randint(1, 9)
                for src, dst in ((a, b), (b, a)):
                    total += 1
                    yield {
                        "_id": det_id("scale", src, dst),
                        "from": src,
                        "to": dst,
                        "type": rng.choice(["shares_device", "shares_address", "shares_pix_key"]),
                        "weight": peso,
                        "created_at": NOW,
                    }
            if i % 20_000 == 0:
                print(f"  {i:,} nós · {total:,} arestas", flush=True)

    enviados = 0
    for lote in chunked(edges(), 3000):
        coll.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in lote], ordered=False)
        enviados += len(lote)
    print(f"  {enviados:,} arestas escritas")

    print("índices…")
    coll.create_index([("from", ASCENDING)])
    coll.create_index([("to", ASCENDING)])
    coll.create_index([("type", ASCENDING), ("from", ASCENDING)])
    print(f"pronto: {coll.estimated_document_count():,} arestas em {COLL}")


def perfil_de_grau(db) -> dict[str, str]:
    """Escolhe um nó de grau alto, um mediano e um baixo."""
    graus = list(
        db[COLL].aggregate(
            [{"$sample": {"size": 60_000}}, {"$group": {"_id": "$from", "g": {"$sum": 1}}}, {"$sort": {"g": -1}}],
            allowDiskUse=True,
        )
    )
    if not graus:
        raise SystemExit("connections_scale vazia — rode --build antes.")
    return {"alto": graus[0]["_id"], "medio": graus[len(graus) // 2]["_id"], "baixo": graus[-1]["_id"]}


def medir(db, entrada: str, profundidade: int, podar: bool, runs: int) -> dict:
    stage = {
        "from": COLL,
        "startWith": "$_id",
        "connectFromField": "to",
        "connectToField": "from",
        "as": "net",
        "maxDepth": profundidade,
        "depthField": "hops",
    }
    if podar:
        stage["restrictSearchWithMatch"] = {"weight": {"$lte": 5}}
    pipeline = [
        {"$match": {"_id": entrada}},
        {"$graphLookup": stage},
        {"$project": {"nos": {"$size": {"$setUnion": ["$net.to", []]}}, "arestas": {"$size": "$net"}}},
    ]
    amostras, ultimo = [], None
    for k in range(runs + 1):
        t0 = time.perf_counter()
        try:
            ultimo = list(db.people.aggregate(pipeline, allowDiskUse=True))
        except OperationFailure as exc:
            # Teto real do $graphLookup, não erro de script: o estágio acumula o
            # traversal inteiro num único array dentro de um documento, e o
            # documento intermediário tem limite de 100 MB. `allowDiskUse` não
            # ajuda — não é spill de sort, é tamanho de documento.
            return {
                "erro": "limite de 100MB do documento de saída",
                "codigo": exc.code,
                "mensagem": str(exc).split(", full error")[0],
                "ms_ate_falhar": round((time.perf_counter() - t0) * 1000, 1),
            }
        dt = (time.perf_counter() - t0) * 1000
        if k:  # descarta o aquecimento
            amostras.append(dt)
    r = ultimo[0] if ultimo else {"nos": 0, "arestas": 0}
    return {
        "nos": r["nos"],
        "arestas": r["arestas"],
        "media_ms": round(statistics.mean(amostras), 1),
        "p95_ms": round(sorted(amostras)[int(len(amostras) * 0.95) - 1], 1),
    }


def measure(db, runs: int) -> dict:
    total = db[COLL].estimated_document_count()
    print(f"{COLL}: {total:,} arestas dirigidas\n")
    entradas = perfil_de_grau(db)
    out: dict = {"medido_em": datetime.now(timezone.utc).isoformat(), "arestas": total, "runs": runs, "perfis": {}}

    for rotulo, pid in entradas.items():
        grau = db[COLL].count_documents({"from": pid})
        print(f"── entrada de grau {rotulo} ({grau} arestas diretas)")
        out["perfis"][rotulo] = {"grau": grau, "profundidades": {}}
        for prof in (1, 2, 3):
            m = medir(db, pid, prof, podar=False, runs=runs)
            out["perfis"][rotulo]["profundidades"][prof] = m
            if m.get("erro"):
                print(f"   depth {prof}: ✗ {m['erro']} após {m['ms_ate_falhar']:.0f} ms")
                # Com poda, o mesmo traversal cabe? É a pergunta que importa.
                mp = medir(db, pid, prof, podar=True, runs=1)
                out["perfis"][rotulo]["profundidades"][f"{prof}_com_poda"] = mp
                if mp.get("erro"):
                    print(f"            com poda: também estoura ({mp['ms_ate_falhar']:.0f} ms)")
                else:
                    print(f"            com poda: {mp['nos']:,} nós · {mp['arestas']:,} arestas · {mp['media_ms']:.0f} ms")
                break
            print(f"   depth {prof}: {m['nos']:>8,} nós · {m['arestas']:>9,} arestas · {m['media_ms']:>9.1f} ms (p95 {m['p95_ms']:.1f})")

    print("\n── impacto da poda, entrada de grau alto, profundidade 1")
    for rotulo, podar in (("sem poda", False), ("com poda", True)):
        m = medir(db, entradas["alto"], 1, podar=podar, runs=runs)
        out.setdefault("poda", {})[rotulo] = m
        if m.get("erro"):
            print(f"   {rotulo}: ✗ {m['erro']}")
        else:
            print(f"   {rotulo}: {m['nos']:>8,} nós · {m['media_ms']:>9.1f} ms")

    destino = Path(__file__).parent / "scale-results.json"
    destino.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nresultados em {destino}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--people", type=int, default=150_000)
    ap.add_argument("--avg-degree", type=int, default=16)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"{COLL} apagada")
        return
    if args.build:
        build(db, args.people, args.avg_degree)
    if args.measure or not args.build:
        measure(db, args.runs)


if __name__ == "__main__":
    main()
