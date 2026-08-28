"""Hierarquia comercial e escopo de visibilidade.

## O que este módulo prova

A pergunta "quais contas este usuário pode ver?" é um traversal, não uma lista
materializada. `advisors.reports_to` é a fonte da verdade; a visibilidade é
derivada dela na hora da consulta, com um `$graphLookup` descendo a árvore.

O contraste com a alternativa comum importa na conversa com o banco: quando a
visibilidade é uma lista pré-calculada por usuário, toda troca de carteira ou
mudança de gestor exige recalcular — e entre o evento e o recálculo alguém vê o
que não devia. Aqui não existe janela: a resposta é sempre derivada do estado
atual da árvore.

## Escopo é decidido no servidor

`visible_to` nunca recebe o conjunto de ids pelo cliente. O frontend manda **quem
é o usuário**; o backend descobre o que ele alcança. É a mesma regra que vale para
a profundidade do traversal no resto da POV: o frontend propõe, o backend decide.
"""
from __future__ import annotations

import time
from typing import Any

from pymongo.errors import OperationFailure

from app.config import get_settings
from app.db.client import get_db, with_retry

# Profundidade da árvore comercial: superintendente → regional → gerente →
# assessor. 4 dá folga de um nível sobre o desenho atual sem abrir a porta para
# um traversal sem teto.
MAX_NIVEIS = 4


def _subordinados_pipeline(advisor_id: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"_id": advisor_id}},
        {"$graphLookup": {
            "from": "advisors",
            "startWith": "$_id",
            "connectFromField": "_id",
            "connectToField": "reports_to",
            "as": "equipe",
            "maxDepth": MAX_NIVEIS,
            "depthField": "nivel_relativo",
        }},
        {"$project": {
            "nome": 1, "papel": 1, "matricula": 1, "regiao": 1, "segmento": 1,
            "nivel": 1,
            "equipe": {"$map": {"input": "$equipe", "as": "e", "in": {
                "id": "$$e._id", "nome": "$$e.nome", "papel": "$$e.papel",
                "matricula": "$$e.matricula", "reports_to": "$$e.reports_to",
                "nivel_relativo": "$$e.nivel_relativo",
            }}},
        }},
    ]


def team(advisor_id: str) -> dict[str, Any] | None:
    """O usuário e todo mundo abaixo dele. Um assessor devolve equipe vazia."""
    docs = with_retry(
        lambda: list(get_db().advisors.aggregate(
            _subordinados_pipeline(advisor_id),
            maxTimeMS=get_settings().graph_max_time_ms,
        )),
        "equipe do assessor",
    )
    return docs[0] if docs else None


def scope_ids(advisor_id: str) -> list[str]:
    """Ids de assessores cuja carteira este usuário pode ver, incluindo ele mesmo."""
    doc = team(advisor_id)
    if not doc:
        return []
    return [advisor_id] + [e["id"] for e in doc["equipe"]]


def portfolio(advisor_id: str, limite_empresas: int = 50) -> dict[str, Any]:
    """Carteira consolidada do escopo do usuário.

    Uma agregação só: desce a hierarquia, junta as empresas de todos os
    assessores alcançados, soma a exposição e devolve as maiores. O `$graphLookup`
    e o `$lookup` da carteira rodam no servidor — o cliente não monta lista de ids
    nem pagina hierarquia.
    """
    s = get_settings()
    started = time.perf_counter()

    pipeline = _subordinados_pipeline(advisor_id) + [
        {"$set": {"escopo": {"$setUnion": [["$_id"], "$equipe.id"]}}},
        # A soma percorre `credit_exposure`, não `companies`.
        #
        # A versão anterior partia das empresas do escopo e fazia `$lookup` da
        # exposição documento a documento: para um regional isso são 160 mil
        # empresas e ~13 s de trabalho no servidor. Só ~32% da base tem crédito,
        # e é a exposição que carrega o número — então o `advisor_id` foi
        # denormalizado para lá (ver `generate_advisors.propaga_para_exposicao`)
        # e a carteira vira um `$match` por índice mais um `$group`.
        {"$lookup": {
            "from": "credit_exposure",
            "let": {"escopo": "$escopo"},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$advisor_id", "$$escopo"]}}},
                {"$group": {
                    "_id": None,
                    "empresas": {"$sum": 1},
                    "limite": {"$sum": "$limite"},
                    "utilizado": {"$sum": "$utilizado"},
                    "vencido": {"$sum": "$vencido"},
                    "top": {"$topN": {
                        "n": limite_empresas,
                        "sortBy": {"utilizado": -1},
                        "output": {
                            "id": "$company_id", "advisor_id": "$advisor_id",
                            "utilizado": "$utilizado", "vencido": "$vencido",
                            "rating": "$rating",
                        },
                    }},
                }},
            ],
            "as": "carteira",
        }},
        {"$set": {"carteira": {"$first": "$carteira"}}},
        # Só as maiores posições são hidratadas com razão social — 50 documentos,
        # não a carteira inteira.
        {"$lookup": {
            "from": "companies",
            "let": {"ids": {"$ifNull": ["$carteira.top.id", []]}},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$_id", "$$ids"]}}},
                {"$project": {"cnpj": 1, "razao_social": 1, "uf": 1}},
            ],
            "as": "nomes",
        }},
    ]

    try:
        docs = with_retry(
            lambda: list(get_db().advisors.aggregate(pipeline, maxTimeMS=s.graph_max_time_ms)),
            "carteira do escopo",
        )
    except OperationFailure as exc:
        return {"found": False, "advisor_id": advisor_id, "error": str(exc).split(", full error")[0]}

    if not docs:
        return {"found": False, "advisor_id": advisor_id}
    doc = docs[0]
    c = doc.get("carteira") or {}
    nomes = {n["_id"]: n for n in doc.get("nomes", [])}
    top = [
        {
            **t,
            "cnpj": nomes.get(t["id"], {}).get("cnpj"),
            "razao_social": nomes.get(t["id"], {}).get("razao_social"),
            "uf": nomes.get(t["id"], {}).get("uf"),
        }
        for t in c.get("top", [])
    ]

    return {
        "found": True,
        "user": {
            "id": doc["_id"], "nome": doc.get("nome"), "papel": doc.get("papel"),
            "matricula": doc.get("matricula"), "regiao": doc.get("regiao"),
            "segmento": doc.get("segmento"), "nivel": doc.get("nivel"),
        },
        "team": doc["equipe"],
        "scope": {
            "advisors": len(doc["equipe"]) + 1,
            "depth": max([e["nivel_relativo"] for e in doc["equipe"]], default=-1) + 1,
        },
        "portfolio": {
            "currency": s.currency,
            # Contas **com crédito** no escopo: é o que a soma cobre, e chamar
            # isso de "contas" sem mais nada seria enganoso.
            "companies_with_credit": c.get("empresas", 0),
            "limite": round(c.get("limite", 0.0), 2),
            "utilizado": round(c.get("utilizado", 0.0), 2),
            "vencido": round(c.get("vencido", 0.0), 2),
            "top": top,
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def can_see(advisor_id: str, cnpj: str) -> dict[str, Any]:
    """O usuário pode ver esta empresa?

    Uma agregação: sobe do assessor dono da conta até o topo e verifica se o
    usuário está no caminho. Subir da folha custa um traversal de no máximo três
    saltos; descer do gerente custaria a árvore inteira dele.
    """
    db = get_db()

    # Uma agregação: parte da empresa, salta para o assessor dono e sobe a cadeia
    # de comando dele. A versão anterior fazia isso em duas idas ao cluster (um
    # `find_one` e depois a agregação) — com um piso de rede alto, a segunda ida
    # custava mais do que todo o trabalho de banco somado.
    docs = with_retry(
        lambda: list(db.companies.aggregate([
            {"$match": {"cnpj": cnpj}},
            {"$limit": 1},
            {"$lookup": {
                "from": "advisors",
                "let": {"dono": "$advisor_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$dono"]}}},
                    {"$graphLookup": {
                        "from": "advisors",
                        "startWith": "$reports_to",
                        "connectFromField": "reports_to",
                        "connectToField": "_id",
                        "as": "superiores",
                        "maxDepth": MAX_NIVEIS,
                    }},
                    {"$project": {"nome": 1, "papel": 1, "superiores": {
                        "$map": {"input": "$superiores", "as": "s",
                                 "in": {"id": "$$s._id", "nome": "$$s.nome",
                                        "papel": "$$s.papel"}}}}},
                ],
                "as": "dono",
            }},
            {"$project": {"razao_social": 1, "advisor_id": 1,
                          "dono": {"$first": "$dono"}}},
        ], maxTimeMS=get_settings().graph_max_time_ms)),
        "cadeia de comando",
    )
    if not docs:
        return {"found": False, "cnpj": cnpj}

    empresa = docs[0]
    if not empresa.get("advisor_id"):
        return {"found": True, "allowed": False, "reason": "empresa sem assessor atribuído"}
    if not empresa.get("dono"):
        return {"found": True, "allowed": False, "reason": "o assessor desta conta não existe mais"}

    doc = empresa["dono"]
    caminho = [doc["_id"]] + [s["id"] for s in doc["superiores"]]
    permitido = advisor_id in caminho
    return {
        "found": True,
        "allowed": permitido,
        "cnpj": cnpj,
        "razao_social": empresa.get("razao_social"),
        "owner": {"id": doc["_id"], "nome": doc.get("nome"), "papel": doc.get("papel")},
        "chain": doc["superiores"],
        "reason": (
            "o usuário está na cadeia de comando do assessor da conta"
            if permitido else
            "a conta pertence a outro ramo da hierarquia"
        ),
    }
