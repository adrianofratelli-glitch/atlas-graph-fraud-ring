#!/usr/bin/env python3
"""Mede a latência dos caminhos que a demo percorre. Regrava `bench-results.json`.

## O que este script mede, e o que ele não mede

Mede o tempo de resposta **do cliente**, incluindo a rede. Por isso a primeira
coisa que ele reporta é o piso de rede (`ping`): sem esse número, toda linha da
tabela é ilegível — uma medição de 275 ms tomada de uma rede com 256 ms de piso
diz respeito à rede, não ao `$graphLookup`.

Compare **incrementos sobre o piso**, não valores absolutos.

A primeira execução de cada cenário é descartada: ela paga o aquecimento do cache
do WiredTiger e mediria a coisa errada.

Uso:
    .venv/bin/python queries/bench.py --runs 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import hierarchy, ownership  # noqa: E402
from app.db.client import get_db  # noqa: E402


def tempo(fn, runs: int) -> dict[str, float]:
    fn()  # descartada: aquecimento
    amostras = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        amostras.append((time.perf_counter() - t) * 1000)
    amostras.sort()
    return {
        "p50": round(statistics.median(amostras), 1),
        "p95": round(amostras[min(len(amostras) - 1, int(len(amostras) * 0.95))], 1),
        "min": round(amostras[0], 1),
        "max": round(amostras[-1], 1),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--out", default="queries/bench-results.json")
    args = p.parse_args()

    db = get_db()
    db.command("ping")

    piso = tempo(lambda: db.command("ping"), args.runs)
    print(f"piso de rede (ping): p50 {piso['p50']} ms")

    # Um grupo por profundidade societária, do mais raso ao mais fundo. Medir só
    # o de quatro níveis escondia a pergunta que o cliente faz: até onde o
    # traversal vai antes de a latência sair do orçamento?
    por_nivel: dict[int, dict] = {}
    for g in db.economic_groups.find({"showcase": True}):
        por_nivel.setdefault(g.get("levels", 0), g)
    if not por_nivel:
        raise SystemExit("Sem grupo de vitrine: rode data-generator/generate_ownership.py.")
    grupo = por_nivel[max(por_nivel)]
    solicitante = db.companies.find_one({"_id": grupo["applicant_id"]}, {"cnpj": 1})
    # Controle: uma empresa comum, sem participação de PJ — o caso da maioria da
    # base, e o contraste que mostra que a demo não escolheu só o caso bonito.
    isolada = db.companies.find_one({"is_holding": {"$ne": True}}, {"cnpj": 1})

    gerente = db.advisors.find_one({"papel": "gerente"}, {"_id": 1})
    assessor = db.advisors.find_one({"papel": "assessor"}, {"_id": 1})

    cenarios: list[tuple[str, str, callable]] = []
    # Cada grupo medido **na sua própria profundidade**: é o traversal alcançando
    # o fim da árvore, não parando antes. É esta a curva que responde "até onde dá".
    for nivel in sorted(por_nivel):
        g = por_nivel[nivel]
        emp = db.companies.find_one({"_id": g["applicant_id"]}, {"cnpj": 1})
        if not emp:
            continue
        cnpj_n = emp["cnpj"]
        cenarios.append(
            (f"grupo econômico de {nivel} níveis, profundidade {nivel}", "traversal",
             lambda c=cnpj_n, n=nivel: ownership.economic_group(c, n))
        )
    # O grupo mais fundo consultado no teto: prova que pedir mais do que a árvore
    # tem não custa latência, porque o traversal termina sozinho.
    cenarios.append(
        (f"grupo mais fundo, profundidade 6 (teto)", "traversal",
         lambda: ownership.economic_group(solicitante["cnpj"], 6))
    )
    cenarios.append(
        ("empresa sem grupo, profundidade 3", "traversal",
         lambda: ownership.economic_group(isolada["cnpj"], 3))
    )
    if gerente:
        cenarios.append(("carteira do gerente", "hierarquia",
                         lambda: hierarchy.portfolio(gerente["_id"])))
    if assessor:
        cenarios.append(("carteira do assessor", "hierarquia",
                         lambda: hierarchy.portfolio(assessor["_id"])))
        cenarios.append(("checagem de visibilidade", "hierarquia",
                         lambda: hierarchy.can_see(assessor["_id"], solicitante["cnpj"])))

    linhas = []
    for nome, familia, fn in cenarios:
        m = tempo(fn, args.runs)
        m["scenario"] = nome
        m["family"] = familia
        m["over_floor_p50"] = round(m["p50"] - piso["p50"], 1)
        linhas.append(m)
        print(f"  {nome:<42} p50 {m['p50']:>7.1f} ms   (+{m['over_floor_p50']:.1f} sobre o piso)")

    relatorio = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runs": args.runs,
        "network_floor_ms": piso,
        "volume": {
            c: db[c].estimated_document_count()
            for c in ("companies", "ownership", "credit_exposure", "people", "advisors")
        },
        "scenarios": linhas,
        "note": (
            "medido do cliente; o piso de rede está incluído em toda linha. "
            "Compare incrementos sobre o piso, não absolutos."
        ),
    }
    Path(args.out).write_text(json.dumps(relatorio, indent=2, ensure_ascii=False))
    print(f"\ngravado em {args.out}")


if __name__ == "__main__":
    main()
