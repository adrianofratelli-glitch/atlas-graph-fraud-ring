"""Exposição financeira da rede que está na tela.

## Por que este arquivo existe, e onde ele para

A POV inteira se sustenta em "medido, não estimado". Um número de negócio
inventado — "economize R$ 2 milhões por ano!" — destruiria isso na primeira
pergunta, e destruiria junto a credibilidade dos benchmarks que foram medidos de
verdade.

Então a divisão aqui é explícita, e a tela repete:

- **medido**: quanto dinheiro passou pelas contas desta rede, em quantas operações
  e em que janela. Isso sai de `transactions`, é um fato do dado, e é o número que
  responde "por que eu deveria me importar com essas 30 contas?".
- **entrada do cliente**: quanto custa investigar um caso hoje. Só o banco sabe.
  Vem de `ANALYST_HOURS_PER_CASE` e `ANALYST_COST_PER_HOUR`, e a resposta carrega
  `assumption: True` para a UI marcar na tela.

O que este módulo deliberadamente **não** faz: estimar perda evitada. Isso exigiria
uma taxa de conversão de exposição em prejuízo que varia por produto, por banco e
por tipologia — chutar seria exatamente o pecado que o resto do projeto evita.
"""
from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.db.client import get_db, with_retry


def network_exposure(person_ids: list[str]) -> dict[str, Any]:
    """Volume, operações e janela das transações que tocam a rede informada."""
    if not person_ids:
        return {"ok": False, "error": "no nodes provided"}

    db = get_db()
    s = get_settings()
    started = time.perf_counter()

    contas = [
        a["_id"]
        for a in with_retry(
            lambda: list(db.accounts.find({"person_id": {"$in": person_ids}}, {"_id": 1})),
            "exposure: contas",
        )
    ]
    if not contas:
        return {"ok": True, "accounts": 0, "operations": 0, "volume": 0.0}

    # Uma agregação só, nos dois sentidos do pagamento: dinheiro que entrou e que
    # saiu das contas da rede é exposição igual — o funil de mulas move nos dois.
    pipeline = [
        {"$match": {"$or": [{"from_account": {"$in": contas}}, {"to_account": {"$in": contas}}]}},
        {
            "$group": {
                "_id": None,
                "operations": {"$sum": 1},
                "volume": {"$sum": "$amount"},
                "ticket": {"$avg": "$amount"},
                "first": {"$min": "$timestamp"},
                "last": {"$max": "$timestamp"},
            }
        },
    ]
    docs = with_retry(
        lambda: list(db.transactions.aggregate(pipeline, allowDiskUse=True)),
        "exposure: agregação",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if not docs:
        return {"ok": True, "accounts": len(contas), "operations": 0, "volume": 0.0,
                "elapsed_ms": elapsed_ms}

    d = docs[0]
    dias = None
    if d.get("first") and d.get("last"):
        dias = max(1, (d["last"] - d["first"]).days)

    custo_caso = s.analyst_hours_per_case * s.analyst_cost_per_hour
    return {
        "ok": True,
        "currency": s.currency,
        # --- medido ---
        "people": len(person_ids),
        "accounts": len(contas),
        "operations": d.get("operations", 0),
        "volume": round(d.get("volume") or 0.0, 2),
        "avg_ticket": round(d.get("ticket") or 0.0, 2),
        "window_days": dias,
        "elapsed_ms": elapsed_ms,
        # --- entrada do cliente, marcada como tal ---
        "assumption": {
            "hours_per_case": s.analyst_hours_per_case,
            "cost_per_hour": s.analyst_cost_per_hour,
            "cost_per_case": round(custo_caso, 2),
            # Um caso hoje é aberto por conta; aqui um caso cobre a rede inteira.
            # A comparação é essa, e ela é aritmética simples sobre o número do
            # cliente — não uma projeção nossa.
            "manual_cases": len(person_ids),
            "manual_cost": round(custo_caso * len(person_ids), 2),
            "graph_cost": round(custo_caso, 2),
            "note": (
                "hours and cost per case are the bank's own numbers, not measurements "
                "from this project"
            ),
        },
    }
