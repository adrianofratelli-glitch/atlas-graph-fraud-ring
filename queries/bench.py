#!/usr/bin/env python3
"""Preenche `queries/benchmarks.md` com números medidos, nunca estimados.

Uso:
    ../.venv/bin/python queries/bench.py --runs 20
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data-generator"))
from common import get_db  # noqa: E402


def timed(fn, runs: int) -> tuple[float, float, object]:
    """Descarta a primeira execução: ela paga o aquecimento de cache do WiredTiger."""
    fn()
    samples, last = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        last = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return round(statistics.mean(samples), 1), round(p95, 1), last


def expand(db, entry, depth, prune=True, edge_types=None):
    stage = {
        "from": "connections",
        "startWith": "$_id",
        "connectFromField": "to",
        "connectToField": "from",
        "as": "network",
        "maxDepth": depth,
        "depthField": "hops",
    }
    clauses = []
    if prune:
        clauses.append({"weight": {"$lte": 50}})
    if edge_types:
        clauses.append({"type": {"$in": edge_types}})
    if clauses:
        stage["restrictSearchWithMatch"] = clauses[0] if len(clauses) == 1 else {"$and": clauses}
    return lambda: list(
        db.people.aggregate(
            [
                {"$match": {"_id": entry}},
                {"$graphLookup": stage},
                {"$project": {"nos": {"$size": {"$setUnion": ["$network.to", []]}}, "arestas": {"$size": "$network"}}},
            ],
            allowDiskUse=True,
        )
    )


def by_device(db, account_id, depth):
    return lambda: list(
        db.transactions.aggregate(
            [
                {"$match": {"from_account": account_id, "ring_id": {"$ne": None}}},
                {"$limit": 1},
                {
                    "$graphLookup": {
                        "from": "transactions",
                        "startWith": "$device_id",
                        "connectFromField": "device_id",
                        "connectToField": "device_id",
                        "as": "network",
                        "maxDepth": depth,
                        "depthField": "hops",
                    }
                },
                {"$project": {"contas": {"$size": {"$setUnion": ["$network.from_account", []]}}}},
            ],
            allowDiskUse=True,
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--out", default=str(Path(__file__).parent / "bench-results.json"))
    args = ap.parse_args()

    db = get_db()
    ring = db.rings.find_one(sort=[("size", -1)])
    # Mesmo ponto de entrada que a UI usa (`services/demo.entry_points`): um nó de
    # nível intermediário. Entrar pelo líder revela demais na profundidade 1;
    # entrar por uma folha exige profundidade além do cap.
    membros = [d["_id"] for d in db.people.find({"ring_id": ring["ring_id"]}, {"_id": 1})]
    graus = list(
        db.connections.aggregate(
            [
                {"$match": {"from": {"$in": membros}}},
                {"$group": {"_id": "$from", "grau": {"$sum": 1}}},
                {"$sort": {"grau": -1}},
            ]
        )
    )
    # Mesmo ponto de entrada da UI (`services/demo._entry_node`): o líder, desde
    # que tenha arestas materializadas.
    com_arestas = {g["_id"] for g in graus}
    entry = ring["leader"] if ring["leader"] in com_arestas else graus[0]["_id"]
    account = db.accounts.find_one({"person_id": entry}, {"_id": 1})["_id"]
    # Para o Padrão A é preciso partir de uma transação *da rede*: a conta também
    # tem tráfego legítimo, e o dispositivo dele é usado só por ela.
    ring_txn = db.transactions.find_one({"from_account": account, "ring_id": {"$ne": None}}, {"_id": 1})
    if ring_txn is None:
        ring_txn = db.transactions.find_one({"ring_id": ring["ring_id"]}, {"from_account": 1})
        account = ring_txn["from_account"]

    ping = timed(lambda: db.command("ping"), args.runs)
    print(f"latência de rede até o cluster (ping): média {ping[0]} ms, p95 {ping[1]} ms")
    print("Todo número abaixo inclui essa latência — o cluster é remoto.\n")

    results: dict = {
        "network_ping": {"mean_ms": ping[0], "p95_ms": ping[1]},
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "runs": args.runs,
        "entry_person": entry,
        "ring": {"id": ring["ring_id"], "size": ring["size"]},
        "counts": {c: db[c].estimated_document_count() for c in ["people", "accounts", "devices", "transactions", "connections"]},
        "pattern_b": {},
        "pattern_a": {},
        "prune": {},
    }

    print("Padrão B (arestas explícitas)")
    for d in range(1, 6):
        mean, p95, last = timed(expand(db, entry, d), args.runs)
        nos = last[0]["nos"] if last else 0
        results["pattern_b"][d] = {"mean_ms": mean, "p95_ms": p95, "nodes": nos}
        print(f"  depth {d}: {mean:>8.1f} ms  p95 {p95:>8.1f} ms  {nos:>6} nós")

    print("\nPadrão A (device_id implícito)")
    for d in range(1, 4):
        mean, p95, last = timed(by_device(db, account, d), args.runs)
        contas = last[0].get("contas", 0) if last else 0
        results["pattern_a"][d] = {"mean_ms": mean, "p95_ms": p95, "accounts": contas}
        print(f"  depth {d}: {mean:>8.1f} ms  p95 {p95:>8.1f} ms  {contas:>6} contas")

    print("\nImpacto da poda de hub (depth 4)")
    for label, prune in [("sem_poda", False), ("com_poda", True)]:
        mean, p95, last = timed(expand(db, entry, 4, prune=prune), args.runs)
        nos = last[0]["nos"] if last else 0
        results["prune"][label] = {"mean_ms": mean, "p95_ms": p95, "nodes": nos}
        print(f"  {label:9s}: {mean:>8.1f} ms  {nos:>6} nós")

    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nresultados em {args.out}")


if __name__ == "__main__":
    main()
