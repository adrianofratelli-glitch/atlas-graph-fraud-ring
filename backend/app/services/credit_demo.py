"""Pontos de entrada da demo de crédito e simulação de alteração societária.

O roteiro depende de haver sempre um caso bom à mão: uma empresa que, olhada
sozinha, passa na esteira — rating bom, sem atraso, limite modesto — e que
pertence a um grupo com inadimplência relevante alguns níveis acima. Esse caso é
preparado pelo gerador e registrado em `economic_groups`; a tela não sai
procurando por ele ao vivo na frente do cliente.

Há também um controle: uma empresa sem grupo, para o primeiro passo mostrar o
caso comum antes de mostrar o interessante.
"""
from __future__ import annotations

import random
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.client import get_db, with_retry

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def entry_points(limit: int = 6) -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache

    db = get_db()
    # A ordem da lista não é arbitrária: o primeiro grupo é o de **construção**.
    #
    # Todo grupo de vitrine tem um setor só, e em quase todos a análise semântica
    # encontra o que precisa. No setor agro, porém, os três CNAEs do grupo
    # ("cultivo de cereais", "moagem de grãos", "criação de bovinos") são
    # genuinamente três negócios diferentes, e o painel — corretamente — não
    # acusa concentração. Abrir a demo por ali é abrir pelo caso em que o
    # argumento não aparece.
    #
    # Construção é o exemplo limpo: "construção de edifícios", "obras de
    # alvenaria" e "serviços de engenharia" são três códigos e um negócio só, e
    # nenhuma palavra em comum entre eles.
    grupos = with_retry(
        lambda: list(
            db.economic_groups.find({"showcase": True})
            .sort([("sector", 1)])
            .limit(limit * 3)
        ),
        "economic_groups",
    )
    grupos.sort(key=lambda g: (g.get("sector") != "construcao", g["_id"]))
    grupos = grupos[:limit]

    solicitantes = []
    for g in grupos:
        emp = db.companies.find_one(
            {"_id": g["applicant_id"]}, {"cnpj": 1, "razao_social": 1, "uf": 1}
        )
        if not emp:
            continue
        cred = db.credit_exposure.find_one({"company_id": g["applicant_id"]})
        solicitantes.append(
            {
                "group_id": g["_id"],
                "company_id": g["applicant_id"],
                "cnpj": emp["cnpj"],
                "razao_social": emp["razao_social"],
                "uf": emp.get("uf"),
                "group_size": g["size"],
                # O que a esteira vê olhando só a empresa: é isto que a demo
                # começa mostrando, para o contraste existir.
                "own_rating": cred["rating"] if cred else None,
                "own_limite": cred["limite"] if cred else 0.0,
                "own_vencido": cred["vencido"] if cred else 0.0,
            }
        )

    # Controle: empresas sem participação societária de PJ, o caso comum.
    controle = []
    for doc in db.companies.find(
        {"is_holding": {"$ne": True}, "situacao": "ATIVA"}, {"cnpj": 1, "razao_social": 1}
    ).limit(400):
        if db.ownership.count_documents({"owned_id": doc["_id"], "owner_type": "corporate"}, limit=1):
            continue
        controle.append({"company_id": doc["_id"], "cnpj": doc["cnpj"], "razao_social": doc["razao_social"]})
        if len(controle) >= 3:
            break

    result = {"applicants": solicitantes, "control": controle}
    with _lock:
        _cache = result
    return result


def simulate_ownership_change(company_ids: list[str] | None = None) -> dict[str, Any]:
    """Insere uma participação societária nova tocando o grupo na tela.

    É o equivalente de uma alteração contratual registrada na Junta Comercial: o
    grupo muda de composição **depois** de a decisão de crédito ter sido tomada, e
    o change stream precisa avisar. Marcada com `simulated: True` para o reset
    conseguir limpar.
    """
    db = get_db()
    if not company_ids:
        return {"ok": False, "error": "no group on screen"}

    alvo = db.companies.find_one(
        {"_id": {"$in": company_ids}, "credit_status": "under_review"}, {"razao_social": 1}
    ) or db.companies.find_one({"_id": {"$in": company_ids}}, {"razao_social": 1})
    if not alvo:
        return {"ok": False, "error": "no company found for the group on screen"}

    rng = random.Random()

    # A empresa adquirida precisa **ter crédito**, senão o alerta chega dizendo
    # que entrou R$ 0 no grupo e o passo não prova nada. O que torna a alteração
    # societária relevante para a mesa é exatamente a exposição que ela arrasta
    # junto — de preferência com atraso.
    candidata = None
    for filtro in (
        {"vencido": {"$gt": 0}},          # melhor caso: já entra com inadimplência
        {"utilizado": {"$gt": 1_000_000}},  # segundo melhor: exposição relevante
        {},                                 # último recurso: qualquer uma com crédito
    ):
        cur = db.credit_exposure.find(
            {**filtro, "company_id": {"$nin": company_ids}},
            {"company_id": 1, "limite": 1, "vencido": 1},
        ).skip(rng.randrange(0, 400)).limit(1)
        achou = next(iter(cur), None)
        if achou:
            candidata = achou
            break
    if not candidata:
        return {"ok": False, "error": "no company with credit available to attach"}

    nova = db.companies.find_one(
        {"_id": candidata["company_id"]}, {"razao_social": 1, "cnpj": 1}
    )
    if not nova:
        return {"ok": False, "error": "no company available to attach"}

    doc = {
        "_id": f"own_sim_{uuid.uuid4().hex[:12]}",
        "owner_id": alvo["_id"],
        "owner_type": "corporate",
        "owned_id": nova["_id"],
        "percentage": round(rng.uniform(51, 100), 2),
        "qualificacao": "socio-administrador",
        "since": datetime.now(timezone.utc),
        "simulated": True,
    }
    db.ownership.insert_one(doc)
    esperado = bool(
        db.companies.count_documents({"_id": alvo["_id"], "credit_status": "under_review"}, limit=1)
    )
    return {
        "ok": True,
        "expect_alert": esperado,
        "explanation": (
            "the acquiring company is under credit review: the listener should publish an alert"
            if esperado
            else "no company in this group is under review: the listener sees the change and does "
            "not alert — that is the correct outcome, not a failure"
        ),
        "acquirer": {"id": alvo["_id"], "name": alvo.get("razao_social")},
        "acquired": {"id": nova["_id"], "name": nova.get("razao_social"), "cnpj": nova.get("cnpj")},
        "edge_id": doc["_id"],
    }
